
from pydantic import BaseModel, Field


class VisitReply(BaseModel):
    """
    Structured conversational output for the home visit booking node.

    The LLM MUST return this schema whenever it is NOT
    invoking `save_visit_tool`.
    """

    reply: str = Field(
        ...,
        description=(
            "The natural language reply sent to the patient. "
            "Use Egyptian Arabic by default and match the patient's tone. "
            "Ask for ONE missing REQUIRED booking field at a time. "

            "PHONE NUMBER COLLECTION RULES: "
            "When the primary phone number is missing, ask the patient "
            "for TWO contact phone numbers in the SAME message, "
            "clearly explaining that the first is REQUIRED and "
            "the second is OPTIONAL. "

            "Example: "
            "'ممكن تبعتلي رقمين للتواصل؟ "
            "الرقم الأول أساسي، والتاني اختياري. "
            "ولو عندك رقم واحد بس ابعته عادي.' "

            "If the patient provides ONE valid phone number, "
            "accept it as phone_number and immediately continue "
            "to the next required booking field. "

            "If the patient provides TWO valid phone numbers, "
            "capture both: phone_number and secondary_phone_number. "

            "NEVER require a second phone number. "
            "NEVER ask for the optional number in a separate message. "
            "NEVER block booking because the second number is missing. "
            "NEVER ask again for information already provided. "
            "NEVER invent or assume missing booking data. "
            "Follow the BOOKING FLOW rules in the system prompt."
        ),
    )

    summary: str = Field(
        ...,
        description=(
            "The updated cumulative booking summary in English. "
            "Preserve ALL previously collected patient information "
            "from conversation history and previous summaries. "

            "Track the following fields: "
            "Name, Phone, Secondary Phone, Address, "
            "Details, Date, Time, and Notes. "

            "Phone = the REQUIRED primary phone number. "
            "Secondary Phone = the OPTIONAL second contact number. "

            "If only one phone number was provided, "
            "store it as Phone and set Secondary Phone "
            "to 'Not provided'. "

            "If two phone numbers were provided, "
            "preserve both numbers separately. "

            "When the patient updates a number, "
            "replace only the corresponding phone field. "

            "Never mark the booking as incomplete solely "
            "because Secondary Phone is missing. "

            "Preserve all known booking fields, "
            "apply new updates, and never invent missing values. "

            "Follow the SUMMARY GUIDELINES "
            "section of the system prompt exactly."
        ),
    )
