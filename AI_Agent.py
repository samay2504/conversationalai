import logging
import os
import sys
import uuid
import json
import asyncio
from typing import Dict, Literal, List
from langgraph.graph import END, START, StateGraph, MessagesState
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver
from concurrent.futures import ThreadPoolExecutor
import re
from error_logger import log_to_openobserve
from langfuse.decorators import langfuse_context, observe
from dotenv import load_dotenv
#from pymongo import MongoClient
from datetime import datetime
import traceback
# Import the MongoDB handler that sets up the connection to MongoDB Cloud
from mongodb_handler import get_context_collection
from AI_tools import *

logging.basicConfig(
    filename="logs/AI_Agent.log",
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

load_dotenv()

class AsyncAIBot:
    MAX_HISTORY = 12   # Maximum number of messages to keep in history
    MAX_REWORK_TRIES = 2 # Supervisor Agent check – we don't want infinite loops

    def __init__(self):
        try:
            self._setup_environment()
            self.tools = [
                get_all_discount,get_detail_about_particular_discount,influencer_collaboration,where_is_my_order,store_locator,warranty_extension,bulk_order,do_it_yourself,faqs,nashermiles_product_info,product_issue_or_complain_registration,order_cancellation_handler,return_exchange_request,payment_issue_handler,product_issue_or_complain_registration_invoice_validation,discount_for_students,refund_status_handler,product_recommendation_handler
            ]
            self.tool_node = ToolNode(self.tools)
            self.model = ChatGoogleGenerativeAI(model="gemini-2.5-flash-preview-05-20", temperature=0.39).bind_tools(self.tools)
            self.memory = MemorySaver()
            self.workflow = self._initialize_workflow()

            # MongoDB Cloud setup using the helper from mongodb_handler.
            self.context_collection = get_context_collection()

            # Load user contexts from MongoDB Cloud.
            self.user_contexts = {}
            self.executor = ThreadPoolExecutor(max_workers=10)
            self.user_id = ""
            self.operator_name=""
        except Exception as e:
            logging.error(f"Error initializing bot: {e}", exc_info=True)

    def _setup_environment(self):
        """
        Sets up the environment variables needed for the LangChain AI model and other components.

        This includes the Google API key, LangChain tracing, endpoint, API key, project name, and model name.
        If any of these values are not set as environment variables, they will be set here.
        """
        try: # here enter your own api keys. 
            os.environ["GOOGLE_API_KEY"] = os.environ.get("GOOGLE_API_KEY")
            os.environ["LANGFUSE_PUBLIC_KEY"] = ""
            os.environ["LANGFUSE_SECRET_KEY"] = ""
            os.environ["LANGFUSE_HOST"] = "https://us.cloud.langfuse.com"
            os.environ["LANGSMITH_TRACING"] = "true"
            os.environ["LANGSMITH_ENDPOINT"] = "https://api.smith.langchain.com"
            os.environ["LANGSMITH_API_KEY"] = ""
            os.environ["LANGSMITH_PROJECT"] = ""
        except Exception as e:
            logging.error(f"Error setting up environment: {e}", exc_info=True)
    @observe()
    def _initialize_workflow(self) -> StateGraph:
        """
        Initializes the LangGraph workflow for the Gogizmo Bot.

        Returns:
            StateGraph: Compiled workflow graph.
        """
        try:
            workflow = StateGraph(MessagesState)
            workflow.add_node("agent", self.call_model)
            workflow.add_node("tools", self.tool_node)

            workflow.add_edge(START, "agent")
            workflow.add_conditional_edges("agent", self.should_continue)
            workflow.add_edge("tools", "agent")

            return workflow.compile()
        except Exception as e:
            logging.error(f"Error initializing workflow: {e}", exc_info=True)
    @observe()
    def _load_contexts(self, user_id: str) -> dict:
        """
        Loads context for a specific user from MongoDB.
        """
        try:
            doc = self.context_collection.find_one({"_id": user_id})
            return doc.get("context_data", {}) if doc else {}
        except Exception as e:
            logging.error(f"Error loading user context for {user_id}: {e}", exc_info=True)
            return {}
    @observe()
    def _save_contexts(self):
        """
        Saves user contexts to MongoDB. Uses upsert to handle both insert and update.
        """
        try:
            user_id = self.user_id
            context_data = self.user_contexts
            if user_id:
                self.context_collection.update_one(
                    {"_id": user_id},
                    {"$set": {"context_data": context_data}},
                    upsert=True
                )
            
        except Exception as e:
            logging.error(f"Error saving user contexts: {e}", exc_info=True)
    @staticmethod
    def should_continue(state: MessagesState) -> Literal["tools", END]:
        """
        If the last message includes a request for tool calls, go to 'tools'. 
        Otherwise, end the chain.
        """
        last_message = state["messages"][-1]
        # print(last_message)
        # print(last_message.tool_calls)
        return "tools" if last_message.tool_calls else END
    @observe()
    async def run_agent(self, user_id: str, query: str, operator_name: str) -> str:
        """
        Main entry point for processing queries (with up to 3 reworks if Supervisor says it doesn't make sense).
        """
        try:
            self.user_id = user_id
            self.operator_name = operator_name
            
            # Load context for this specific user
            self.user_contexts = self._load_contexts(user_id)
            
            

            # --- CONTACT-INTENT DETECTION LOGIC ---
            # Check if query has any contact-related keywords
            # keywords = ["call", "ring", "contact", "talk"]
            # if any(kw in query.lower() for kw in keywords):
                

            #     predicted_intent = await call_intent.check_intent(self.user_id,query)
            #     if predicted_intent == "CONTACT_INTENT":
            #         # Return your custom callback message
            #         final_reply="We will arrange a callback as soon as possible. Expect a call between 9 AM and 9 PM. For more details,  I request you to contact to this number 8407084070 / 9884601008"
            #         now_str = datetime.datetime.now().strftime('%d-%m-%Y %H:%M:%S')

            #         self._append_to_history(
            #             self.user_id,
            #             role="user",
            #             content=f"{now_str} : {query}"
            #         )
            #         self._append_to_history(
            #             self.user_id,
            #             role="bot",
            #             content=f"{now_str} : {final_reply}"
            #         )

            #         self._save_contexts()
            #         return final_reply

            # 3) Default: normal conversation logic
            final_reply = await self._attempt_with_supervision(user_id, query)

            
            now_str = datetime.now().strftime('%d-%m-%Y')

            self._append_to_history(
                self.user_id,
                role="user",
                content=f"{now_str} : {query}"
            )
            self._append_to_history(
                self.user_id,
                role="bot",
                content=f"{now_str} : {final_reply}"
            )

            self._save_contexts()
            return final_reply

        except Exception as e:
            await log_to_openobserve("AI_Agent",traceback.format_exc(),level="critical")
            logging.error(f"Error running agent for user {user_id}: {e}", exc_info=True)
            logging.error(traceback.format_exc())
            print(traceback.format_exc())
            return "I'm sorry, I'm having trouble processing your request. Please try again later."
    
    # >>> CHANGED: Pass in the tool calls to the supervisor check so it can see if scheduling was really called.
    @observe()
    async def _attempt_with_supervision(self, user_id: str, query: str) -> str:
        """
        Attempt to get a valid reply from the main agent. 
        The Supervisor Agent checks if it "makes sense."
        We do up to 3 attempts if the Supervisor says it's invalid.
        """
        tries = 0
        final_response = ""
        final_query = query

        # while tries < self.MAX_REWORK_TRIES:
            # 1) Process user query with the main agent
        response_dict = await self.process_single_query(user_id, query)
            # bot_text = response_dict["final_text"]
            # bot_tool_calls = response_dict["tool_calls"]

            # # 2) Ask Supervisor if it's valid
            # is_valid, reason_or_message = await self._supervisor_check(
            #     user_id,
            #     final_query,
            #     bot_text,
            #     bot_tool_calls
            # )

            # if is_valid:
            #     final_response = bot_text
            #     break
            # else:
            #     final_response = bot_text
            #     break
                # tries += 1
                # # Rework by prepending supervisor's reason to the user's query
                # query = f"(Supervisor says: {reason_or_message}) {final_query}"

                # if tries == self.MAX_REWORK_TRIES:
                #     final_response = bot_text

        return response_dict["final_text"]

    # >>> CHANGED: We now return both the final text and the tool calls.
    @observe()
    async def process_single_query(self, user_id: str, query: str) -> dict:
        """
        Process a single query asynchronously with the compiled workflow.
        Returns a dict containing 'final_text' and 'tool_calls'.
        """
        try:
            state = {
                "messages": [HumanMessage(content=query)],
            }
            cfg = {
                "thread_id": str(uuid.uuid4()),
                "checkpoint_ns": "phone_agent",
                "checkpoint_id": str(uuid.uuid4()),
            }

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self.executor,
                self.workflow.invoke,
                state,
                cfg
            )

            # >>> ADDED: Collect all messages
            messages_list = result["messages"]
            
            # >>> ADDED: Gather ANY tool calls that appear in ANY of the messages
            all_tool_calls = []
            for m in messages_list:
                if hasattr(m, "tool_calls") and m.tool_calls:
                    all_tool_calls.extend(m.tool_calls)
            

            # Suppose `all_tool_calls` is the list of all tool calls made during the conversation
            final_all_tool_calls = []
            for tool_call in all_tool_calls:
                if isinstance(tool_call, dict) and tool_call.get("name") == "schedule_visit":
                    final_all_tool_calls.append(tool_call)


            # The final message is typically the final LLM response to the user
            final_message = messages_list[-1]
            bot_text = final_message.content.strip() if final_message.content else ""

            

            return {
                "final_text": bot_text,
                "tool_calls": final_all_tool_calls
            }

        except Exception as e:
            logging.error(f"Error processing query for user {user_id}: {query}", exc_info=True)
            return {
                "final_text": "I'm sorry, I'm having trouble processing your request. Please try again later.",
                "tool_calls": []
            }

    @observe()
    def call_model(self, state: MessagesState):
        messages = state["messages"]
        current_message = messages[0].content

        # Extract context data
        phone_number=self.user_contexts.get("Phone_Number", "")
        email=self.user_contexts.get("email", "Not Available")
        last_response = self.user_contexts.get("Last_Response", "")
        conversation_history = self.user_contexts.get("conversation_history", [])
        operator=self.operator_name
        from datetime import date
        today = date.today()

        # Retrieve last 12 messages
        recent_history = self._get_recent_history(conversation_history, limit=14)
        
        # Format them for the prompt
        history_prompt = ""
        for entry in recent_history:
            if entry['role'] == 'user':
                history_prompt += f"Customer: {entry['content']}\n"
            elif entry['role'] == 'bot':
                history_prompt += f"{self.operator_name}: {entry['content']}\n"
          
        SYSTEM_PROMPT = f"""
        You are {self.operator_name}, a customer support representative for *Nasher Miles*, a premium luggage brand known for high-quality travel bags & accessories and excellent customer service.
        CUSTOMER SUPPORT  
        Phone/WhatsApp: +91 99300 76456  
        Email: customercare@nashermiles.com  
        Website: https://www.nashermiles.com  
        Support Hours: 9 AM to 9 PM IST  
--------------------------------------------------------
SYSTEM BEHAVIOR & GUIDELINES

1. TOOL ACCESS & USAGE RULES

Available tools:  
get_all_discount,get_detail_about_particular_discount, where_is_my_order, return_refund_policy, influencer_collaboration, warranty_extension, bulk_order, store_locator, nashermiles_product_info, order_cancellation, return_exchange_request, product_issue_or_complain_registration,product_issue_or_complain_registration_invoice_validation,discount_for_students,faqs,product_recommendation_handler

Use these tools only when clearly needed. Never fabricate details or rely on external/internal data. All actions must come from user context and tools.

- **Specific discount** → get_detail_about_particular_discount  
- **All discounts** → get_all_discount
- **When the customer is interested to know a solution for their product which have some issue or shows intent to fix it themselves** → do_it_yourself
- **Order status or Order tracking** → where_is_my_order 
- **Warranty extension**:  
  1. Ask for **email ID**  
  2. Ask to upload **invoice**  
  3. As soon as upload appears (URL), use `warranty_extension`  
- **Product issue complaint or Missing/Wrong item Complaint**(use when customer is showing intent to register a complaint):  
  1. Ask for **email ID**
  2. Ask for **size of bag**
  3. Ask for **invoice and photo of the product one by one**(always ask for invoice first)  
     - If invoice appears and is valid → use `product_issue_or_complain_registration_invoice_validation` to check the validity of the invoice
     - If invoice is invalid or unreadable → Request a valid one   
     - As soon as image (URL) is visible → use `product_issue_or_complain_registration`   
- **Discount for students**(when customer wants to avail student discount then only ask for relevant details) :
    1. Ask for **email ID**
    2. Ask for **admission card or school ID**
    3. As soon as image (URL) is visible → use `discount_for_students`
- **Bulk orders** → bulk_order(ask all the relevant details from the customer in once do not ask again if answered) 
- **Any product-related query (price, features, dimensions, colors, availability, etc.)** → nashermiles_product_info(call ONCE with the complete query - this tool provides ALL product information in a single response) 
- **Product recommendations or suggestions or information about sale ** → product_recommendation_handler
- **Order cancellation ask for order number starting with NM and the reason for cancellation** → order_cancellation   
- **Payment issues** (e.g. debited but no order):  
    Ask for proof of payment and other details → trigger tool once file is visible
- **FAQ** → faqs(when customer is asking about information about locks, return policy, warranty policy, cancellation policy, or other queries related policies)

Upload Behavior:

- Proceed **automatically** with the tool only if:
  1. The required uploads appear in the conversation, **and**
  2. The operation (e.g., warranty extension, product issue registration) has **not already been successfully completed**.
- If the operation **has already been successfully completed**, and the customer sends a new upload (e.g., invoice, image, ID):
  → Do **not** call any tool again.
  → Instead, ask politely: “I see you've uploaded a file. Could you please let me know the reason for this upload, since we’ve already completed the process?”
- After a successful operation, **reset `try_count` to 1** to avoid unnecessary retries or misclassification of follow-up uploads.
- Never assume new intent based solely on an uploaded file unless the customer explicitly states the reason.


2. RESPONSE & CONTEXT HANDLING
- Understand the full conversation context and current customer query before replying.
- Never repeat questions already asked in the chat history.
- Responses should be smooth, logical, and focused on resolving the customer's issue.
- Do not modify tool responses.
- Use different emojis in the response.
- Keep replies short, polite, human, and helpful.
- If the customer provides only a single piece of information and intent is not clearly stated in the conversation, ask a clarifying question before taking any action.
- Clarify only when needed.
- Avoid repeating links(reduces readability) .
- For cases requiring uploads (warranty extension, product issues, etc.): always list *all* required details upfront — including the email address. Then, collect each item step-by-step in the correct order.
→ If email is required and NOT present in the context: "I'll need your email address, the size of the bag (Small, Medium, or Large), a copy of your invoice, and a photo of the damaged product. Let's start with your email address."
→ If email is required and IS present in the context: "I'll need your email address, the size of the bag (Small, Medium, or Large), a copy of your invoice, and a photo of the damaged product. Let's start with your confirming your email address. I see you've used {email} earlier — would you like to proceed with this one or provide a new one?"
⚠️ Do not proceed to the next step until the valid email address is confirmed.

3. CONVERSATION FLOW
- Greet the customer warmly with a welcome to Nasher Miles and ask how you may assist them. 
- Identify missing steps using conversation history — never repeat completed ones.
- For upload-related cases (warranty, product issues, student discounts, payment issues): list all required details first in order, then collect them step-by-step in order.
  (Adapt the phrasing naturally based on context — do not use fixed scripts.)
- Ask for uploads only when needed. Proceed automatically once files appear — no need for customer confirmation.
- Use the correct tool after gathering all required information.
- End each message with a clear, polite next step or confirmation.

4. STRICT RULES
- Do not copy above examples in your response. Adapt it according to the conversation.
- Never ask customers to upload a URL — ask for the actual file.
- Do not use or mention external/internal knowledge, tools, automation, or system logic.
- Do not reveal your reasoning process or identity as an AI.
- Always validate tool output before responding.
- If a tool fails or lacks information, apologize and offer a callback or follow-up.
- **TOOL CALL EFFICIENCY**: Each tool should be called only ONCE per user query unless the workflow explicitly requires multiple calls. The nashermiles_product_info tool provides complete product information in one response - never call it multiple times for the same product.

--------------------------------------------------------
CONTEXT:
Current Date: {datetime.now().strftime('%d-%m-%Y')}
(Customer's Email: {email})
--------------------------------------------------------
User_id: {self.user_id}
--------------------------------------------------------
• Conversation History:\n  
{history_prompt}
--------------------------------------------------------
• Last Response by {self.operator_name}:  
{last_response}
--------------------------------------------------------
• Customer's Current Message:  
{current_message}
--------------------------------------------------------

COMPLIANCE:  
Strictly follow tone, tool logic, file handling rules, and conversation awareness. Revise your response if it breaks any of the guidelines.
"""





        
        # Prepend system message if we only have the user's single message.
        if len(messages) == 1 and isinstance(messages[0], HumanMessage):
            messages = [
                SystemMessage(content=SYSTEM_PROMPT),
                messages[0]
            ]

        response=self.model.invoke(messages)
        
        # If LLM decides to use a tool, inject user_id
        # if hasattr(response, "tool_calls"):
        #     for tool_call in response.tool_calls:
        #         if isinstance(tool_call, dict):
        #             if tool_call.get("name") in ["get_all_discount"]:
        #                 if "args" not in tool_call:
        #                     tool_call["args"] = {}
        #                 tool_call["args"]["user_id"] = self.user_id
        #         else:
        #             logging.warning(f"Unexpected tool_call type: {type(tool_call)}")
        
        # Bot's textual response
        bot_response = response.content.strip() if response.content else ""

        self.user_contexts = self._load_contexts(self.user_id)
        # Update context in memory
        self.user_contexts["Last_Response"] = bot_response
        print(response)
        self._save_contexts()
        return {"messages": [response]}

    # >>> CHANGED: We pass the list of tool calls, then specifically check if {self.operator_name} claims scheduling but never invoked 'schedule_visit'.
    @observe()
    async def _supervisor_check(self, user_id: str, user_query: str, bot_reply: str, tool_calls: list):
        """
        Calls the Supervisor Agent (gemini-1.5-flash-002) to verify if the final bot reply
        makes sense given the last 6 conversation lines, the current user query,
        and the tool calls used.

        If {self.operator_name} claims scheduling but never called `schedule_visit`, we respond DOES_NOT_MAKE_SENSE.
        Otherwise respond accordingly.
        """
        conversation_history = self.user_contexts.get("conversation_history", [])
        last_response=self.user_contexts.get("Last_Response", "")
        recent_history = self._get_recent_history(conversation_history, limit=6)
        operator=self.operator_name

        # Format conversation for Supervisor
        history_text = ""
        for entry in recent_history:
            if entry['role'] == 'user':
                history_text += f"User: {entry['content']}\n"
            elif entry['role'] == 'bot':
                history_text += f"{self.operator_name}: {entry['content']}\n"
        if len(tool_calls) > 0:
            tool_calls="Tool Called :schedule_visit"
        # Convert tool calls to JSON string for passing into LLM
        tool_calls_str = json.dumps(tool_calls, indent=2)
        print(type(tool_calls_str))
    
        
        SUPERVISOR_PROMPT = f"""
You are the supervisor monitoring customer interactions at Ribbons Jewellery. Your task is to review the customer-agent conversation to ensure the overall flow makes sense and the communication is smooth.

### TASK:
Your focus is to review the conversation as a whole and verify if it flows logically, addressing the customer's query appropriately. Do not focus on the type of response or guideline adherence, but instead assess if the conversation makes sense in terms of the flow.

1. **Clarity & Continuity**: Ensure the conversation is clear and continues logically from one message to the next. The customer's query should be addressed properly, and the flow should not break or appear disjointed.
2. **Customer Journey**: Ensure the interaction follows a proper customer journey, starting with the greeting, progressing through the inquiry, and ending with the correct resolution.
3. **Relevance**: Check that the responses are relevant to the customer's needs. Ensure there are no irrelevant responses or interruptions in the conversation.
4. "pp" is a slang word for "price please". Customer's use this word when they want to know the price.

### RESPONSE OPTIONS:
- If the conversation makes sense and flows properly, respond with: **"MAKES_SENSE"**.
- If the conversation does not make sense or the flow is broken, respond with: **"DOES_NOT_MAKE_SENSE: <reason in under 20 words>"**.
- Do not include asterisks in your response.

### CONTEXT:
- **Conversation History**:
  {history_text}

- **Customer's Current Query**:
  {user_query}

- **{self.operator_name}'s Response**:
  {bot_reply}
### IMPORTANT:
- Do not focus on the operator's style or guidelines. Only assess whether the conversation makes sense and if the flow is appropriate.
- Do not make any suggestions about changing responses or offering specific improvements—just flag if the conversation as a whole is incoherent or broken.
- Ensure the customer's journey through the conversation is smooth and logical.

"""


        supervisor_llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash", temperature=0.0)
        llm_response = supervisor_llm.invoke(SUPERVISOR_PROMPT)
        text = llm_response.content.strip()
        print(text)
        
        # Parse supervisor verdict
        if text.startswith("MAKES_SENSE"):
            return True, "All good"
        elif text.startswith("DOES_NOT_MAKE_SENSE"):
            reason_match = re.match(r"DOES_NOT_MAKE_SENSE\s*:\s*(.*)", text, re.IGNORECASE)
            if reason_match:
                reason = reason_match.group(1).strip()
            else:
                reason = "No reason found"
            return False, reason
        else:
            # If supervisor's response is unclear, treat as valid by default
            return True, "Unclear (default to valid)"

    def _is_affirmative(self, message: str) -> bool:
        affirmative_responses = [
            "yes", "sure", "yeah", "please do", 
            "go ahead", "yes please", "absolutely", "yep"
        ]
        return message.strip().lower() in affirmative_responses

    def _is_suggestion(self, message: str) -> bool:
        # Simple heuristic to detect if the bot made a suggestion
        suggestion_keywords = ["suggest", "recommend", "how about", "may i suggest", "maybe you would like"]
        return any(keyword in message.lower() for keyword in suggestion_keywords)

    def _extract_suggestion(self, message: str) -> str:
        # Extract the suggestion from the message
        match = re.search(
            r"suggest(?:ion|ed)?\s+(?:the\s+)?(.+?)(?:\.|,|;|$)", 
            message, 
            re.IGNORECASE
        )
        if match:
            return match.group(1).strip()
        return ""

    def _append_to_history(self, user_id: str, role: str, content: str):
        """
        Append a message to the user's conversation history, up to MAX_HISTORY.
        """
        history = self.user_contexts.get("conversation_history", [])
        history.append({"role": role, "content": content})
        self.user_contexts["conversation_history"] = history

    def _get_recent_history(self, history: List[Dict[str, str]], limit: int = 6) -> List[Dict[str, str]]:
        """Retrieve the most recent messages up to the specified limit."""
        return history[-limit:] if len(history) > limit else history

import time
from datetime import datetime

async def main():
    start_time = time.time()
    bot = AsyncAIBot()
    input_query = input("Enter user_id: ")
    user_id = input_query
    new_doc = {
            "_id": user_id,
            "context_data": {
                "first_message": "",
                "name": "Rahul",
                "Phone_Number": "",
                "email": "test@test.com",
                "channel": "instagram",
                "instagram_username": "username",
                "Last_Response": "",
                "last_suggestion": "",
                "conversation_history": []
            }
        }
    get_context_collection().insert_one(new_doc)
    from gemini_live import upload_to_gcs_public
    while True:
        query = input("Enter your query: ")
        new_query=query
        if query.startswith("https://"):
            new_query=upload_to_gcs_public(query)
        if query.lower() == "exit":
            break
        

        response = await bot.run_agent(user_id, new_query,"Mohit")
        # print(response)
        # print("************************************************")
        # Optionally, process the final response with the 'human_response' method
        # query_result = await human_response(str(response))
        print(response)
        # print(query_result)

    # print("--- %s seconds ---" % (time.time() - start_time))

if __name__ == "__main__":
    asyncio.run(main())
