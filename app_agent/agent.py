import os
import json
from dotenv import load_dotenv
from typing import Any, Dict, List

# --- Google GenAI configuration ---
import google.genai as genai

load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")

if not API_KEY:
    raise RuntimeError(
        "FATAL ERROR: 'GOOGLE_API_KEY' not found in .env. "
        "Please edit .env and add: GOOGLE_API_KEY='your_key_here'"
    )

# --- ADK imports (version 1.16.0 compatible) ---
from google.adk.agents.llm_agent import LlmAgent


from google.adk.tools.function_tool import FunctionTool

# --- Import the custom tool ---
# --- Mau was here ---

from .tools import write_score_to_sheet

MODEL_NAME = os.getenv("MODEL", "gemini-1.5-pro")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "session_state.json")

# --- Load data files safely ---
try:
    with open(os.path.join(BASE_DIR, "questions.json"), encoding="utf-8") as f:
        questions_data_full = json.load(f)
        questions_data = {q["variable"]: q["question"] for q in questions_data_full.get("questions", [])}
except FileNotFoundError:
    print(f"FATAL ERROR: 'questions.json' not found in {BASE_DIR}.")
    questions_data, questions_data_full = {}, {"questions": []}

try:
    with open(os.path.join(BASE_DIR, "criteria.json"), encoding="utf-8") as f:
        criteria_data_full = json.load(f)
        criteria_data = {c["variable"]: c.get("criteria", []) for c in criteria_data_full.get("criteria", [])}
except FileNotFoundError:
    print(f"FATAL ERROR: 'criteria.json' not found in {BASE_DIR}.")
    criteria_data, criteria_data_full = {}, {"criteria": []}

VARIABLES = [q["variable"] for q in questions_data_full.get("questions", [])]

# --- Function Tools ---
@FunctionTool
def advance_loop_state(state: dict) -> dict:
    """Moves to the next variable only when current stage is complete."""
    if state.get("hitl_stage") is not None:
        return state
    if state.get("loop_finished"):
        return state

    if "remaining_variables" not in state:
        state["remaining_variables"] = VARIABLES.copy()

    if not state["remaining_variables"]:
        state["loop_finished"] = True
        return state

    current_var = state["remaining_variables"].pop(0)
    state["current_variable"] = current_var
    state.setdefault("asked_questions", {})[current_var] = []
    state.setdefault("answers", {})[current_var] = []

    q_text = questions_data.get(current_var, f"What information do you have for {current_var}?")
    state["current_question"] = {"text": q_text}
    state["hitl_stage"] = "await_question"
    return state


@FunctionTool
def get_next_question(state: dict) -> dict:
    """Fetches the next question for the current variable."""
    current_var = state.get("current_variable")
    if not current_var:
        state["current_question"] = {"text": "Error: no active variable."}
        return state

    asked = state.get("asked_questions", {}).get(current_var, [])
    q = questions_data.get(current_var, None)

    if not q:
        state["hitl_stage"] = "await_scoring"
        state["current_question"] = {"text": "No questions found for this variable."}
        return state

    if q in asked:
        state["hitl_stage"] = "await_scoring"
        state["current_question"] = {"text": "All questions have been asked."}
        return state

    state["current_question"] = {"text": q}
    state["hitl_stage"] = "await_user"
    return state


@FunctionTool
def record_answer(state: dict, answer: str) -> dict:
    """Stores user input and moves to scoring."""
    var = state.get("current_variable")
    q = state.get("current_question", {}).get("text")
    if not var or not q:
        return state

    ans = answer.strip()
    if var == "Project_Name":
        state["project_name"] = ans
        state["hitl_stage"] = None
        return state

    state.setdefault("answers", {}).setdefault(var, []).append({"question": q, "answer": ans})
    state.setdefault("asked_questions", {}).setdefault(var, []).append(q)
    state.setdefault("ready_for_scoring", []).append(var)
    state["hitl_stage"] = "await_scoring"
    return state


@FunctionTool
def generate_score_prompt(state: dict) -> dict:
    """Generates the text prompt for scoring."""
    var = state.get("current_variable")
    if not var:
        return state

    qa_pairs = state.get("answers", {}).get(var, [])
    criteria_list = criteria_data.get(var, [])
    criteria_text = json.dumps(criteria_list)
    qa_summary = "\n".join([f"Q: {qa['question']}\nA: {qa['answer']}" for qa in qa_pairs])

    prompt = f"""
    You are a project evaluator.
    Criteria for {var}: {criteria_text}
    User answers:
    {qa_summary}
    Respond with a single numeric score (0–100).
    """

    state.setdefault("score_prompts", {})[var] = prompt
    return state


@FunctionTool
def save_final_score(state: dict, score: str) -> dict:
    """Writes score to state + sheet."""
    var = state.get("current_variable")
    project = state.get("project_name", "Unknown Project")
    if not var:
        return state

    try:
        cleaned = int("".join(filter(str.isdigit, score)))
    except ValueError:
        cleaned = 0

    state.setdefault("scores", {})[var] = cleaned
    print(f"{var}: {cleaned}")
    write_score_to_sheet(project, var, str(cleaned), state)
    state["hitl_stage"] = None
    return state


# --- AGENTS ---

questions_agent = LlmAgent(
    name="questions_agent",
    model=MODEL_NAME,
    instruction="""
    You are the Question Agent. Call `get_next_question()` to fetch the next question.
    Then display `state.current_question.text` and wait for the human to respond.
    """,
    tools=[get_next_question],
)

answer_agent = LlmAgent(
    name="answer_agent",
    model=MODEL_NAME,
    instruction="""
    You are the Answer Agent. Accept the user's message as input and call
    `record_answer(answer=<user's response>)`.
    """,
    tools=[record_answer],
)

scorer_agent = LlmAgent(
    name="scorer_agent",
    model=MODEL_NAME,
    instruction="""
    You are the Scoring Agent.
    1. Call `generate_score_prompt()` to prepare the evaluation.
    2. Read `state.score_prompts[var]` and calculate a numeric score.
    3. Call `save_final_score(score=str(your_score))`.
    """,
    tools=[generate_score_prompt, save_final_score],
)

root_agent = LlmAgent(
    name="root_agent",
    model=MODEL_NAME,
    instruction="""
    You are the Root Orchestrator.
    Follow this deterministic logic:

    - If state['hitl_stage'] == 'await_user': call `answer_agent`.
    - If state['hitl_stage'] == 'await_question': call `questions_agent`.
    - If state['hitl_stage'] == 'await_scoring': call `scorer_agent`.
    - If state['hitl_stage'] is None: call `advance_loop_state`.
    - If state['loop_finished'] == True: thank the user and exit.
    """,
    tools=[advance_loop_state],
    sub_agents=[questions_agent, answer_agent, scorer_agent],
)
