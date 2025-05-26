import os
import logging
import azure.functions as func
from openai import AzureOpenAI
import json
from datetime import datetime
import requests
from requests.auth import HTTPBasicAuth

app = func.FunctionApp()

# Determine priority, urgency, and impact based on keywords
def analyze_severity(description: str):
    desc = description.lower()

    if any(k in desc for k in ["outage", "system down", "critical", "major"]):
        return "1", "1", "1"  # High
    elif any(k in desc for k in ["slow", "delay", "performance"]):
        return "2", "2", "2"  # Medium
    elif any(k in desc for k in ["vpn", "access", "login", "reset"]):
        return "3", "2", "2"  # Low
    else:
        return "4", "3", "3"  # Very Low / default

@app.route(route="create-incident", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def create_incident(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Creating new incident based on short description")

    try:
        # Parse request JSON body
        req_body = req.get_json()
        short_description = req_body.get("short_description")

        if not short_description:
            return func.HttpResponse("Missing 'short_description'", status_code=400)

        # Determine severity
        priority, urgency, impact = analyze_severity(short_description)

         # Load secrets from environment
        openai_api_key = os.environ["AZURE_OPENAI_KEY"]
        openai_endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
        servicenow_user = os.environ["SERVICENOW_USERNAME"]
        servicenow_pass = os.environ["SERVICENOW_PASSWORD"]

        
        # Step 1: Get recommended resolution from Azure OpenAI
        client = AzureOpenAI(
            api_key=openai_api_key,  # Replace with your actual key
            api_version="22024-11-20",
            azure_endpoint=openai_endpoint  # Replace with your endpoint
        )

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an IT support assistant. Provide a resolution for a short issue description."},
                {"role": "user", "content": f"Issue: {short_description}"}
            ],
            temperature=0.5,
            max_tokens=500
        )

        resolution = response.choices[0].message.content.strip()

        # Step 2: Create incident in ServiceNow
        servicenow_url = "https://dev276870.service-now.com/api/now/table/incident"

        incident_payload = {
            "short_description": short_description,
            "description": short_description,
            "priority": priority,
            "urgency": urgency,
            "impact": impact
        }

        create_response = requests.post(
            servicenow_url,
            auth=HTTPBasicAuth(servicenow_user, servicenow_pass),
            headers={"Content-Type": "application/json"},
            data=json.dumps(incident_payload)
        )

        if create_response.status_code not in [200, 201]:
            logging.error("Failed to create incident")
            return func.HttpResponse(
                json.dumps({"error": "Failed to create incident", "details": create_response.text}),
                status_code=500,
                mimetype="application/json"
            )

        incident_data = create_response.json()["result"]
        incident_sys_id = incident_data["sys_id"]

        # Step 3: Update the incident with AI-generated resolution in work_notes
        update_url = f"{servicenow_url}/{incident_sys_id}"
        update_payload = {
            "work_notes": resolution
        }

        update_response = requests.patch(
            update_url,
            auth=HTTPBasicAuth(servicenow_user, servicenow_pass),
            headers={"Content-Type": "application/json"},
            data=json.dumps(update_payload)
        )

        if update_response.status_code not in [200, 204]:
            logging.warning("Incident created but failed to update work_notes")

        # Step 4: Return incident info
        return func.HttpResponse(
            json.dumps({
                "message": "Incident created and updated with AI resolution.",
                "incident_sys_id": incident_sys_id,
                "recommended_resolution": resolution
            }),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error: {str(e)}", exc_info=True)
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json"
        )
