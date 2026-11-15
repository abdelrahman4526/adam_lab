import logging

from langchain_core.messages import HumanMessage, SystemMessage

from graph.prompts.direct_prompt import DIRECT_SYSTEM_PROMPT
from graph.state import AgentState
from llm.llm import get_gemini
from schemas.direct import DirectResponse
from services.messaging.client_service import ClientService
from services.domain.laboratory_service import LaboratoryService
from utils.history_utils import get_chat_history
from utils.llm_utils import extract_token_usage

logger = logging.getLogger(__name__)


def direct_node(state: AgentState) -> dict:
    """
    Handle direct/general conversational messages.

    Flow:
        Get Context
        → Load Lab Info from DB
        → Load History
        → Build Prompt
        → Structured Gemini
        → Return State
    """

    # 1. Get conversation context
    user_message = state.get("user_message", "")
    current_summary = state.get("summary") or ""
    last_bot_message = state.get("last_bot_message") or ""

    platform_id = state.get("platform_id")
    page_id = state.get("page_id")
    sender_id = state.get("sender_id")

    # 2. Fetch dynamic Lab Info from DB
    lab_name, lab_location, lab_desc = (
        LaboratoryService.get_current_lab_info()
    )

    # Fetch branches from MySQL
    branches_context = (
        LaboratoryService.get_current_lab_branches_context()
    )

    logger.info(
        "[BOOKING] Loaded laboratory branches context: %s",
        branches_context
    )

    # 3. Load recent conversation history
    client_service = ClientService(
        platform_id=platform_id,
        page_id=page_id,
        sender_id=sender_id,
    )

    history, recent_history = get_chat_history(
        state,
        client_service,
    )

    logger.info(
        "[DIRECT] Processing message | history=%d",
        len(history),
    )

    # 4. Build the LLM prompt with dynamic Lab details
    system_prompt = f"""
{DIRECT_SYSTEM_PROMPT}

====================
LABORATORY INFORMATION
====================
Name: {lab_name}

Description: {lab_desc}
====================
AVAILABLE LABORATORY BRANCHES
====================
{branches_context}

====================
BRANCH INFORMATION RULES
====================
- Branch information is retrieved directly from the laboratory database.
- When the patient asks about available branches, list only the branches provided above.
- When asked about a specific branch, provide its exact name, address, phone number, and working hours when available.
- Never invent branch names, addresses, phone numbers, or working hours.
- If no branch information is available, clearly state that the information is unavailable.
- Do not claim that a booking has been assigned to a specific branch unless that assignment has actually been saved.


====================
MEMORY & CONTEXT
====================

Summary:
{current_summary or "None"}

Last Bot Message:
{last_bot_message or "None"}

Recent History:
{recent_history or "None"}
"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_message),
    ]

    # 5. Generate structured response
    try:
        llm = get_gemini()

        structured_llm = llm.with_structured_output(
            DirectResponse,
            include_raw=True,
        )

        result = structured_llm.invoke(messages)

        parsed = result.get("parsed")

        if parsed is None:
            raise ValueError(
                "Gemini returned no parsed DirectResponse."
            )

        tokens = extract_token_usage(
            result.get("raw")
        ) or {}

        # 6. Return updated graph state
        return {
            "response": parsed.reply,
            "summary": parsed.summary,
            "last_bot_message": parsed.reply,
            "direct_usage": tokens,
        }

    except Exception as exc:
        logger.exception(
            "[DIRECT] Failed to generate response: %s",
            exc,
        )

        fallback = (
            "عذراً، حدث خطأ مؤقت أثناء معالجة رسالتك. "
            "يرجى المحاولة مرة أخرى."
        )

        return {
            "response": fallback,
            "summary": current_summary,
            "last_bot_message": fallback,
            "direct_usage": None,
        }