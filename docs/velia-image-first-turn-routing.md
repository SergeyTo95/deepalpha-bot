# Velia Images first-turn routing regression

The VELIA chat prompt starts the transcript as:

```text
Conversation:
USER: ...
```

The original image-intent extractor required either the beginning of the complete prompt or two newline characters before `USER:`. That meant the first message in a new conversation was invisible to Velia Images, while later user turns were visible because transcript messages are separated with two newline characters.

The fixed extractor accepts a `USER:` marker after any line boundary and uses the same helper for intent detection and original-message localization.
