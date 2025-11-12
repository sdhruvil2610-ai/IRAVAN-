import os
import sys
import json
import asyncio
import logging
from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from app_agent.agent import root_agent, STATE_FILE, VARIABLES

# --------------------------------------------------------------------
# INIT
# --------------------------------------------------------------------

class MessageShim:
    """Minimal shim to provide.role and.parts attributes"""
    def __init__(self, role: str, text: str):
        self.role = role
        self.parts = [{"text": text}]

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------
# SESSION STATE HANDLING
# --------------------------------------------------------------------

def load_session_state() -> dict:
    """Loads state from file or bootstraps a new session."""
    try:
        path = STATE_FILE if os.path.isabs(STATE_FILE) else os.path.join(BASE_DIR, os.path.basename(STATE_FILE))
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                logging.info("♻️  Loaded previous session state.")
                return json.load(f)
    except Exception as e:
        logging.warning(f" ⚠️  Could not load session state: {e}")

    # Bootstrap session state if none exists
    logging.info("🌱  Bootstrapping new session state.")
    return {
        "variables": VARIABLES,
        "remaining_variables": VARIABLES.copy(),
        "current_iteration": 0,
        "loop_finished": False,
        "hitl_stage": None, # Start at None to trigger advance_loop_state
        "scores": {},
        "answers": {},
    }

# --------------------------------------------------------------------
# MAIN EXECUTION LOGIC (INTERACTIVE)
# --------------------------------------------------------------------

async def run():
    state = load_session_state()
    logging.info(" 🤖  Starting Interactive Project Evaluation Workflow…")
    
    session_service = InMemorySessionService()
    session = await session_service.create_session(user_id="default_user", app_name="project_eval_v4")
    session_id = getattr(session, "id", None) or getattr(session, "session_id", None)
    logging.info(f" 🧠  Created/Loaded session: {session_id}")
    
    runner = Runner(agent=root_agent, session_service=session_service, app_name="project_eval_v4")
    
    # The first message is hardcoded to start the agent's logic
    message = MessageShim(role="user", text="start project evaluation")

    # --- This is the new interactive loop ---
    while not state.get("loop_finished", False):
        print("---------------------------------")
        if message and message.role == "user":
            logging.info(f"[User → Agent]: {message.parts['text']}")
        else:
            logging.info("[Agent]: (Thinking...)")

        try:
            async for event in runner.run_async(
                user_id="default_user",
                session_id=session_id,
                new_message=message,
                state_delta=state,
            ):
                etype = getattr(event, "type", None)
                if etype == "message":
                    content = getattr(event, "content", None)
                    if not content:
                        continue
                    text = "".join(
                        p.get("text", "") if isinstance(p, dict) else getattr(p, "text", "")
                        for p in content.parts
                    )
                    # Print the agent's message
                    logging.info(f"[Agent → User]: {text}")

                elif etype == "state":
                    # ALWAYS capture the most recent state
                    state = event.state
                    try:
                        with open(STATE_FILE, "w", encoding="utf-8") as f:
                            json.dump(state, f, indent=2, ensure_ascii=False)
                    except Exception as e:
                        logging.warning(f" ⚠️  Could not save session state: {e}")

                    # --- CRITICAL HITL CHECK ---
                    # If the agent is waiting for a user, break this async
                    # loop to fall through to the input() prompt below.
                    if state.get("hitl_stage") == "await_user":
                        break
                
                elif etype == "error":
                    logging.error(f" ❌  {getattr(event, 'message', event)}")

            # --- END OF 'async for' LOOP ---
            
            # Check the *reason* we exited the async loop
            if state.get("hitl_stage") == "await_user":
                # The agent has asked a question and is now paused.
                # We must now block and wait for the human.
                print("\n")
                user_answer = input("→ Your Answer: ")
                message = MessageShim(role="user", text=user_answer)
            
            elif state.get("loop_finished"):
                # The agent set loop_finished to True.
                print("---------------------------------")
                logging.info(" ✅  Evaluation completed successfully.")
                break # Exit the 'while' loop
            
            else:
                # The agent's turn finished, but it's not waiting for a user.
                # This means it's "thinking" (e.g., scoring, advancing state).
                # We send 'None' as the message to trigger the next
                # turn without user input.
                message = None

        except KeyboardInterrupt:
            logging.info(" 🛑  Interrupted by user — progress saved.")
            break
        except Exception as e:
            logging.exception(f" ❌  Unhandled exception: {e}")
            break

# --------------------------------------------------------------------
# ENTRY POINT
# --------------------------------------------------------------------
if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logging.info(" 🛑  Manual stop received. Session saved.")
        sys.exit(0)