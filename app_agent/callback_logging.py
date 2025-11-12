import logging
import google.cloud.logging
from google.adk.agents.callback_context import CallbackContext  # ✅ correct path for 1.16
from google.adk.models import LlmResponse, LlmRequest

# Initialize Cloud Logging once to avoid multiple clients
client = google.cloud.logging.Client()
client.setup_logging()


def log_query_to_model(callback_context: CallbackContext, llm_request: LlmRequest):
    """Logs user prompts sent to the model."""
    if llm_request.contents and llm_request.contents[-1].role == "user":
        parts = llm_request.contents[-1].parts
        if parts and parts[-1].text:
            logging.info(f"[User → {callback_context.agent_name}]: {parts[-1].text}")


def log_model_response(callback_context: CallbackContext, llm_response: LlmResponse):
    """Logs model responses sent to the user."""
    if llm_response.content and llm_response.content.parts:
        for part in llm_response.content.parts:
            if part.text:
                logging.info(f"[{callback_context.agent_name} → User]: {part.text}")
