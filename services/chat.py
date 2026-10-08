from cache.ttl import TTLCache, make_key
from llm.client import GigaChatClient
from llm.postprocess import clean_response
from llm.prompts import build_prompt
from services.observability import event, stage


class ChatService:
    def __init__(self, settings, client=None, cache=None):
        self.settings = settings
        self.client = client or GigaChatClient(settings)
        self.cache = cache or TTLCache(settings.cache_ttl_seconds, settings.cache_max_entries)

    def summarize(self, message):
        with stage("business_logic"):
            event("scenario_selected", scenario="summarize")
            with stage("prompt_builder"):
                payload = build_prompt(message, self.settings)
                event("prompt_built", prompt=payload)
            with stage("cache_lookup"):
                key = make_key(payload, self.settings.base_url, self.settings.scope)
                answer = self.cache.get(key)
                cached = answer is not None
                event("cache_hit" if cached else "cache_miss", cache_key=key)
            if not cached:
                with stage("llm_call"):
                    raw = self.client.generate(payload)
                with stage("postprocessing"):
                    answer = clean_response(raw)
                    event("answer_cleaned", answer=answer)
                with stage("cache_write"):
                    self.cache.set(key, answer)
            return {"answer": answer, "cached": cached, "scenario": "summarize",
                    "model": self.settings.model}
