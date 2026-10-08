from llm.client import InvalidLLMResponse


def clean_response(data):
    if not isinstance(data, dict):
        raise InvalidLLMResponse()
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise InvalidLLMResponse()
    choice = choices[0]
    if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
        raise InvalidLLMResponse()
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise InvalidLLMResponse()
    content = message.get("content")
    if not isinstance(content, str):
        raise InvalidLLMResponse()
    answer = " ".join(content.split())
    if not answer:
        raise InvalidLLMResponse()
    return answer
