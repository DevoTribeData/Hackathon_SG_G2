from azure.core.exceptions import HttpResponseError
from datetime import datetime
from functools import cached_property
from html import escape
from logging import Logger
from models.call import CallStateModel
from models.message import MessageModel
from models.next import NextModel
from models.reminder import ReminderModel
from models.synthesis import SynthesisModel
from models.training import TrainingModel
from openai.types.chat import ChatCompletionSystemMessageParam
from pydantic import TypeAdapter, BaseModel
from textwrap import dedent
from typing import Optional
import json


class SoundModel(BaseModel):
    loading_tpl: str = "{public_url}/loading.wav"
    ready_tpl: str = "{public_url}/ready.wav"

    def loading(self) -> str:
        from helpers.config import CONFIG

        return self.loading_tpl.format(
            public_url=CONFIG.resources.public_url,
        )

    def ready(self) -> str:
        from helpers.config import CONFIG

        return self.ready_tpl.format(
            public_url=CONFIG.resources.public_url,
        )


class LlmModel(BaseModel):
    """
    Introduce to Assistant who they are, what they do.

    Introduce a emotional stimuli to the LLM, to make is lazier (https://arxiv.org/pdf/2307.11760.pdf).
    """

    default_system_tpl: str = """
        Assistant is called {bot_name} and is working in a call center for company {bot_company} as an expert with 20 years of experience. {bot_company} is a well-known and trusted company. Assistant is proud to work for {bot_company}.

        Always assist with care, respect, and truth. This is critical for the customer.

        # Context
        - The call center number is {bot_phone_number}
        - The customer is calling from {phone_number}
        - Today is {date}
    """
    chat_system_tpl: str = """
        # Objective
        {task}

        # Rules
        - After an action, explain clearly the next step
        - Always continue the conversation to solve the conversation objective
        - Answers in {default_lang}, but can be updated with the help of a tool
        - Ask 2 questions maximum at a time
        - Be concise
        - Enumerations are allowed to be used for 3 items maximum (e.g., "First, I will ask you for your name. Second, I will ask you for your email address.")
        - If you don't know how to respond or if you don't understand something, say "I don't know" or ask the customer to rephrase it
        - Is allowed to make assumptions, as the customer will correct them if they are wrong
        - Provide a clear and concise summary of the conversation at the beginning of each call
        - Respond only if it is related to the objective or the claim
        - To list things, use bullet points or numbered lists
        - Use a lot of discourse markers, fillers, to make the conversation human-like
        - Use tools as often as possible and describe the actions you take
        - When the customer says a word and then spells out letters, this means that the word is written in the way the customer spelled it (e.g., "I live in Paris PARIS" -> "Paris", "My name is John JOHN" -> "John", "My email is Clemence CLEMENCE at gmail dot com" -> "clemence@gmail.com")
        - Work for {bot_company}, not someone else
        - Write acronyms and initials in full letters (e.g., "The appointment is scheduled for eleven o'clock in the morning", "We are available 24 hours a day, 7 days a week")

        # Definitions

        ## Means of contact
        - By SMS, after the call
        - By voice, now with the customer (voice recognition may contain errors)

        ## Actions
        Each message in the story is preceded by a prefix indicating where the customer said it from: {actions}

        ## Styles
        In output, you can use the following styles to add emotions to the conversation: {styles}

        # Context

        ## Reminders
        A list of reminders to help remember to do something: {reminders}

        # How to handle the conversation

        ## New conversation
        1. Understand the customer's situation
        2. Gather general information to understand the situation
        3. Make sure the customer is safe
        4. Gather detailed information about the situation
        5. Advise the customer on what to do next

        ## Ongoing conversation
        1. Synthesize the previous conversation
        2. Ask for updates on the situation
        3. Advise the customer on what to do next
        4. Take feedback from the customer

        # Response format
        style=[style] [response]

        ## Example 1 
		Conversation objective: Help the client setup a meeting with their bank advisor. The client wants to make an appointment and expresses their availabilities. You will try and find an available appointment that works for the client and the advisor.
		Assistant: Hello and welcome to SG, your virtual assistant is at your service. How can I assist you today?
		User: action=talk I want to make an appointment with my bank advisor.
		Tools: book meeting, update necessary information from the client in order to check for potential availabilities
		Assistant: style=none I will assist you in making an appointment. Could you please indicate the purpose of the appointment?
		User: action=talk I have a problem with my card.
		Assistant: style=none Thank you for that clarity. For me to check the availability of your advisor, please let me know when you are available, for example, indicate Monday, July 15th afternoon or Friday, July 19th morning.
		User: action=talk I am available next Wednesday afternoon.
		Tools: get advisor available slot, returns the first available slot in the time frame given by the client.
		Assistant: I am checking the availability of your advisor and will get back to you in a few seconds. There is availability on Wednesday, July 17th at 4pm, does that work for you?
		User: action=talk OK.
		Assistant: I confirm your appointment on Wednesday, July 17th at 4pm. You will find a confirmation of this appointment on your application. Thank you for your loyalty.
		Tools: end call
 
		## Example 2
		Conversation objective: You are a vocal assistant. Guide the client through the process of setting up an appointment.
		Assistant: Hello and welcome to SG, your virtual assistant is at your service. How can I assist you today?
		User: action=talk I want to make an appointment with my bank advisor.
		Tools: book meeting, update necessary information from the client in order to check for potential availabilities
		Assistant: style=none I will assist you in making an appointment. Could you please indicate the purpose of the appointment?
		User: action=talk I would like to take out a home insurance policy.
		Assistant: style=none Thank you for that clarification. For me to check the availability of your advisor, please let me know when you are available, for example, indicate Monday, July 15th in the afternoon or Friday, July 19th in the morning.
		User: action=talk Thursday, July 18th in the morning.
		Tools: get advisor available slot, returns the first available slot in the time frame given by the client.
		Assistant: I am checking the availability of your advisor and will get back to you in a few seconds.
		Assistant: style=sad I'm sorry, but none of your advisor's availability matches. Could you provide me with another half-day availability, different from Thursday, July 18th in the morning?
		User: action=talk Monday, July 22nd afternoon.
		Tools: get advisor available slot, returns the first available slot in the time frame given by the client.
		Assistant: I am checking the availability of your advisor and will get back to you in a few seconds. There is availability on Monday, July 22th at 3pm, does that work for you?
		User: action=talk Yes, that works for me.
		Assistant: I confirm your appointment on Monday, July 22th at 3pm. You will find a confirmation of this appointment on your application. Thank you for your loyalty.
		Tools: end call

    """
    sms_summary_system_tpl: str = """
        # Objective
        Summarize the call with the customer in a single SMS. The customer cannot reply to this SMS.

        # Rules
        - Answers in {default_lang}, even if the customer speaks another language
        - Be concise
        - Can include personal details about the customer
        - Do not prefix the response with any text (e.g., "The respond is", "Summary of the call")
        - Include details stored in the claim, to make the customer confident that the situation is understood
        - Include salutations (e.g., "Have a nice day", "Best regards", "Best wishes for recovery")
        - Refer to the customer by their name, if known
        - Use simple and short sentences
        - Won't make any assumptions

        # Context

        ## Conversation objective
        {task}

        ## Claim
        {claim}

        ## Reminders
        {reminders}

        ## Conversation
        {messages}

        # Response format
        Hello, I understand [customer's situation]. I confirm [next steps]. [Salutation]. {bot_name} from {bot_company}.

        ## Example 1
        Hello, I understand you had a car accident in Paris yesterday. I confirm the appointment with the garage is planned for tomorrow. Have a nice day! {bot_name} from {bot_company}.

        ## Example 2
        Hello, I understand your roof has holes since yesterday's big storm. I confirm the appointment with the roofer is planned for tomorrow. Best wishes for recovery! {bot_name} from {bot_company}.

        ## Example 3
        Hello, I had difficulties to hear you. If you need help, let me know how I can help you. Have a nice day! {bot_name} from {bot_company}.
    """
    synthesis_system_tpl: str = """
        # Objective
        Synthetize the call.

        # Rules
        - Answers in English, even if the customer speaks another language
        - Be concise
        - Consider all the conversation history, from the beginning
        - Don't make any assumptions

        # Context

        ## Conversation objective
        {task}

        ## Claim
        {claim}

        ## Reminders
        {reminders}

        ## Conversation
        {messages}

        # Response format in JSON
        {format}
    """
    citations_system_tpl: str = """
        # Objective
        Add Markdown citations to the input text. Citations are used to add additional context to the text, without cluttering the content itself.

        # Rules
        - Add as many citations as needed to the text to make it fact-checkable
        - Be concise
        - Only use exact words from the text as citations
        - Treats a citation as a word or a group of words
        - Use claim, reminders, and messages extracts as citations
        - Use the same language as the text
        - Won't make any assumptions
        - Write citations as Markdown abbreviations at the end of the text (e.g., "*[words from the text]: extract from the conversation")

        # Context

        ## Claim
        {claim}

        ## Reminders
        {reminders}

        ## Input text
        {text}

        # Response format
        text\\n
        *[extract from text]: "citation from claim, reminders, or messages"

        ## Example 1
        The car accident of yesterday.\\n
        *[of yesterday]: "That was yesterday"

        ## Example 2
        Holes in the roof of the garden shed.\\n
        *[in the roof]: "The holes are in the roof"

        ## Example 3
        You have reported a claim following a fall in the parking lot. A reminder has been created to follow up on your medical appointment scheduled for the day after tomorrow.\\n
        *[the parking lot]: "I stumbled into the supermarket parking lot"
        *[your medical appointment]: "I called my family doctor, I have an appointment for the day after tomorrow."
    """
    next_system_tpl: str = """
        # Objective
        Choose the next action from the company sales team perspective. The respond is the action to take and the justification for this action.

        # Rules
        - Answers in English, even if the customer speaks another language
        - Be concise
        - Take as priority the customer satisfaction
        - Won't make any assumptions
        - Write no more than a few sentences as justification

        # Context

        ## Conversation objective
        {task}

        ## Claim
        {claim}

        ## Reminders
        {reminders}

        ## Conversation
        {messages}

        # Response format in JSON
        {format}
    """

    def default_system(self, call: CallStateModel) -> str:
        from helpers.config import CONFIG

        return self._format(
            self.default_system_tpl.format(
                bot_company=call.initiate.bot_company,
                bot_name=call.initiate.bot_name,
                bot_phone_number=CONFIG.communication_services.phone_number,
                date=datetime.now(call.tz()).strftime(
                    "%a %d %b %Y %H:%M (%Z)"
                ),  # Don't include secs to enhance cache during unit tests.
                phone_number=call.initiate.phone_number,
            )
        )

    def chat_system(
        self, call: CallStateModel, trainings: list[TrainingModel]
    ) -> list[ChatCompletionSystemMessageParam]:
        from models.message import (
            ActionEnum as MessageActionEnum,
            StyleEnum as MessageStyleEnum,
        )

        return self._messages(
            self._format(
                self.chat_system_tpl,
                actions=", ".join([action.value for action in MessageActionEnum]),
                bot_company=call.initiate.bot_company,
                claim=json.dumps(call.claim),
                default_lang=call.lang.human_name,
                reminders=TypeAdapter(list[ReminderModel])
                .dump_json(call.reminders, exclude_none=True)
                .decode(),
                styles=", ".join([style.value for style in MessageStyleEnum]),
                task=call.initiate.task,
                trainings=trainings,
            ),
            call=call,
        )

    def sms_summary_system(
        self, call: CallStateModel
    ) -> list[ChatCompletionSystemMessageParam]:
        return self._messages(
            self._format(
                self.sms_summary_system_tpl,
                bot_company=call.initiate.bot_company,
                bot_name=call.initiate.bot_name,
                claim=json.dumps(call.claim),
                default_lang=call.lang.human_name,
                messages=TypeAdapter(list[MessageModel])
                .dump_json(call.messages, exclude_none=True)
                .decode(),
                reminders=TypeAdapter(list[ReminderModel])
                .dump_json(call.reminders, exclude_none=True)
                .decode(),
                task=call.initiate.task,
            ),
            call=call,
        )

    def synthesis_system(
        self, call: CallStateModel
    ) -> list[ChatCompletionSystemMessageParam]:
        return self._messages(
            self._format(
                self.synthesis_system_tpl,
                claim=json.dumps(call.claim),
                format=json.dumps(SynthesisModel.model_json_schema()),
                messages=TypeAdapter(list[MessageModel])
                .dump_json(call.messages, exclude_none=True)
                .decode(),
                reminders=TypeAdapter(list[ReminderModel])
                .dump_json(call.reminders, exclude_none=True)
                .decode(),
                task=call.initiate.task,
            ),
            call=call,
        )

    def citations_system(
        self, call: CallStateModel, text: str
    ) -> list[ChatCompletionSystemMessageParam]:
        """
        Return the formatted prompt. Prompt is used to add citations to the text, without cluttering the content itself.

        The citations system is only used if `text` param is not empty, otherwise `None` is returned.
        """
        return self._messages(
            self._format(
                self.citations_system_tpl,
                claim=json.dumps(call.claim),
                reminders=TypeAdapter(list[ReminderModel])
                .dump_json(call.reminders, exclude_none=True)
                .decode(),
                text=text,
            ),
            call=call,
        )

    def next_system(
        self, call: CallStateModel
    ) -> list[ChatCompletionSystemMessageParam]:
        return self._messages(
            self._format(
                self.next_system_tpl,
                claim=json.dumps(call.claim),
                format=json.dumps(NextModel.model_json_schema()),
                messages=TypeAdapter(list[MessageModel])
                .dump_json(call.messages, exclude_none=True)
                .decode(),
                reminders=TypeAdapter(list[ReminderModel])
                .dump_json(call.reminders, exclude_none=True)
                .decode(),
                task=call.initiate.task,
            ),
            call=call,
        )

    def _format(
        self,
        prompt_tpl: str,
        trainings: Optional[list[TrainingModel]] = None,
        **kwargs: str,
    ) -> str:
        # Remove possible indentation then render the template
        formatted_prompt = dedent(prompt_tpl.format(**kwargs)).strip()

        # Format trainings, if any
        if trainings:
            # Format documents for Content Safety scan compatibility
            # See: https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/content-filter?tabs=warning%2Cpython-new#embedding-documents-in-your-prompt
            trainings_str = "\n".join(
                [
                    f"<documents>{escape(training.model_dump_json(exclude=TrainingModel.excluded_fields_for_llm()))}</documents>"
                    for training in trainings
                ]
            )
            formatted_prompt += "\n\n# Internal documentation you can use"
            formatted_prompt += f"\n{trainings_str}"

        # Remove newlines to avoid hallucinations issues with GPT-4 Turbo
        formatted_prompt = " ".join(
            [line.strip() for line in formatted_prompt.splitlines()]
        )

        self.logger.debug(f"Formatted prompt: {formatted_prompt}")
        return formatted_prompt

    def _messages(
        self, system: str, call: CallStateModel
    ) -> list[ChatCompletionSystemMessageParam]:
        messages = [
            ChatCompletionSystemMessageParam(
                content=self.default_system(call),
                role="system",
            ),
            ChatCompletionSystemMessageParam(
                content=system,
                role="system",
            ),
        ]
        self.logger.debug(f"Messages: {messages}")
        return messages

    @cached_property
    def logger(self) -> Logger:
        from helpers.logging import logger

        return logger


class TtsModel(BaseModel):
    tts_lang: str = "fr-FR"
    calltransfer_failure_tpl: str = (
        "Il semble que je ne puisse pas vous mettre en relation avec un agent pour le moment, mais le prochain agent disponible vous rappellera dès que possible."
    )
    connect_agent_tpl: str = (
        "Permettez-moi de vous transférer à un agent qui pourra vous aider davantage. Je ne sais pas encore répondre à cette question. Restez en ligne et je vous recontacterai dans les plus brefs délais."
    )
    end_call_to_connect_agent_tpl: str = (
        "Bien sûr, restez en ligne, je vous transfère à un agent."
    )
    error_tpl: str = "Pardon, pourriez-vous répéter votre demande ?"
    goodbye_tpl: str = (
        "Merci d'avoir appelé, j'espère avoir pu vous aider. {bot_company} vous remercie de votre confiance. Gros bisous."
    )
    hello_tpl: str = (
        "Bonjour, je suis {bot_name}, l'assistant virtuel de {bot_company} ! Voici comment je fonctionne : pendant que je traite vos informations, vous entendrez une musique. N'hésitez pas à me parler de manière naturelle - je suis conçu pour comprendre vos demandes."
    )
    timeout_silence_tpl: str = (
        "Je suis désolé, je n'ai pas entendu. Dites-moi comment je peux vous aider ?"
    )
    welcome_back_tpl: str = (
        "Bonjour, je suis l'assistant virtuel {bot_name}, de {bot_company} !"
    )
    timeout_loading_tpl: str = (
        "Il me faut plus de temps que prévu pour vous répondre. Merci de votre patience..."
    )
    ivr_language_tpl: str = "Pour continuer en {label}, appuyez sur {index}."

    async def calltransfer_failure(self, call: CallStateModel) -> str:
        return await self._translate(self.calltransfer_failure_tpl, call)

    async def connect_agent(self, call: CallStateModel) -> str:
        return await self._translate(self.connect_agent_tpl, call)

    async def end_call_to_connect_agent(self, call: CallStateModel) -> str:
        return await self._translate(self.end_call_to_connect_agent_tpl, call)

    async def error(self, call: CallStateModel) -> str:
        return await self._translate(self.error_tpl, call)

    async def goodbye(self, call: CallStateModel) -> str:
        return await self._translate(
            self.goodbye_tpl,
            call,
            bot_company=call.initiate.bot_company,
        )

    async def hello(self, call: CallStateModel) -> str:
        return await self._translate(
            self.hello_tpl,
            call,
            bot_company=call.initiate.bot_company,
            bot_name=call.initiate.bot_name,
        )

    async def timeout_silence(self, call: CallStateModel) -> str:
        return await self._translate(self.timeout_silence_tpl, call)

    async def welcome_back(self, call: CallStateModel) -> str:
        from helpers.config import CONFIG

        return await self._translate(
            self.welcome_back_tpl,
            call,
            bot_company=call.initiate.bot_company,
            bot_name=call.initiate.bot_name,
            conversation_timeout_hour=CONFIG.conversation.callback_timeout_hour,
        )

    async def timeout_loading(self, call: CallStateModel) -> str:
        return await self._translate(self.timeout_loading_tpl, call)

    async def ivr_language(self, call: CallStateModel) -> str:
        res = ""
        for i, lang in enumerate(call.initiate.lang.availables):
            res += (
                self._return(
                    self.ivr_language_tpl,
                    index=i + 1,
                    label=lang.human_name,
                )
                + " "
            )
        return await self._translate(res.strip(), call)

    def _return(self, prompt_tpl: str, **kwargs) -> str:
        """
        Remove possible indentation in a string.
        """
        return dedent(prompt_tpl.format(**kwargs)).strip()

    async def _translate(self, prompt_tpl: str, call: CallStateModel, **kwargs) -> str:
        """
        Format the prompt and translate it to the TTS language.

        If the translation fails, the initial prompt is returned.
        """
        from helpers.translation import translate_text

        initial = self._return(prompt_tpl, **kwargs)
        translation = None
        try:
            translation = await translate_text(
                initial, self.tts_lang, call.lang.short_code
            )
        except HttpResponseError as e:
            self.logger.warning(f"Failed to translate TTS prompt: {e}")
            pass
        return translation or initial

    @cached_property
    def logger(self) -> Logger:
        from helpers.logging import logger

        return logger


class PromptsModel(BaseModel):
    llm: LlmModel = LlmModel()  # Object is fully defined by default
    sounds: SoundModel = SoundModel()  # Object is fully defined by default
    tts: TtsModel = TtsModel()  # Object is fully defined by default
