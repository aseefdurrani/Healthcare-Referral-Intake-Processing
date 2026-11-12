# OpenAI extraction in the synthetic referral demo

This demo uses AWS for document storage and OCR, then calls OpenAI directly for structured extraction. **Use synthetic referrals only.** The former Bedrock implementation remains in `backend/app/adapters/llm.py` as an inactive, tested reference. The application never constructs a Bedrock runtime client.

## Set up the local backend

1. In the [OpenAI Platform](https://platform.openai.com/), create a project, configure billing and a small project usage limit, and create a **project API key** for your own local development. The project key is separate from your AWS profile. [OpenAI key guidance](https://developers.openai.com/api/docs/guides/api-keys)
2. In `backend/.env`, add `OPENAI_API_KEY=<your-key>` and keep `AI_ENABLED=true` and `OPENAI_MODEL_ID=gpt-6.1-terra`. If `backend/.env` does not exist, use `backend/.env.example` as the template and fill in the other local settings. `.env` is ignored by Git. Do not put the key in Next.js, a `NEXT_PUBLIC_` variable, source code, logs, or this document. The backend refuses to start the configured S3/AI workflow when AI is enabled and the key is missing.
3. Keep `AWS_PROFILE`, `AWS_REGION`, `S3_BUCKET_NAME`, the database settings, and Cognito settings configured for the existing AWS stages. The AWS profile authenticates S3 and Textract; it does not authenticate OpenAI.
4. From `backend/`, run `uv sync --locked`. Apply migration `0003_openai_demo` with the existing PostgreSQL admin setup (`uv run alembic upgrade head`), then run the backend with `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --reload`. Alembic reads admin credentials from the shell, not `backend/.env`; see `backend/migrations/env.py`.

For a deployed application, use a project service account and server-side secret manager instead of a developer-owned key. [OpenAI key guidance](https://developers.openai.com/api/docs/guides/api-keys)

## Follow one referral

1. The staff user uploads a synthetic PDF or image through `POST /api/v1/referrals`. The backend stores the original bytes privately in S3 and records a document and processing run in PostgreSQL.
2. Textract's asynchronous `StartDocumentAnalysis` reads the S3 object. The backend collects the result, stores the merged raw Textract JSON as a gzipped S3 object, and stores normalized OCR data in `ocr_runs.normalized`.
3. `render_for_llm` in `backend/app/services/ocr_document.py` turns the normalized OCR into text with page and source IDs such as `[p1-l001]`, form values, checkboxes, and tables. The model receives this **text transcript**, not the PDF, S3 URL, or raw Textract JSON.
4. `OpenAILanguageModel.extract` in `backend/app/adapters/llm_openai.py` loads the system prompt from `backend/app/ai/prompts/referral_extract_v1.txt` and the JSON schema from `backend/app/ai/schema/referral_extraction_v1.json`. It sends the transcript to the Responses API. The official SDK supplies the API key as a server-side authentication header.
5. The backend accepts only a completed, non-refusal response whose `output_text` parses as `ReferralExtractionV1` and whose `document_ref` matches the current document. It checks evidence quotes against OCR lines, maps values to referral fields, applies deterministic validation flags, and opens staff review. The staff member approves or corrects the result; the model never approves or routes a referral autonomously.

The request body sent by the SDK has this shape; `input` is a string containing the actual synthetic OCR transcript:

```python
client.responses.create(
    model="gpt-6.1-terra",
    instructions=load_prompt(),
    input="document_ref: <document-uuid>\npage_count: 1\n<<<OCR_DOCUMENT ...>>>\n[p1-l001] Jordan Sample\n<<<END_OCR_DOCUMENT>>>",
    text={"format": {
        "type": "json_schema",
        "name": "referral_extraction_v1",
        "schema": load_schema(),
        "strict": True,
    }},
    reasoning={"effort": "medium"},
    max_output_tokens=6000,
    store=False,
)
```

There are no `tools`, file inputs, image inputs, or `previous_response_id`. The SDK request timeout is at most 90 seconds and is shortened to the remaining processing-run deadline. The SDK has no automatic transport retry; a transient error becomes a reviewable run failure that staff may retry. An incomplete or invalid model output gets **one** additional call with a 12,000-token output limit if time remains. Both calls use the same prompt and OCR transcript.

A successful response body contains an output message and usage metadata. This is an abbreviated, synthetic envelope; the `text` value is the full JSON object defined in the schema:

```json
{
  "id": "resp_example123",
  "status": "completed",
  "output": [{
    "type": "message",
    "content": [{
      "type": "output_text",
      "text": "<complete referral_extraction_v1 JSON object>"
    }]
  }],
  "usage": {"input_tokens": 1200, "output_tokens": 900}
}
```

The backend consumes these SDK fields (illustrative synthetic values):

```text
response.status              = "completed"
response.output_text         = '{"document_ref":"<document-uuid>","patient":{...},...}'
response.usage.input_tokens  = 1_200
response.usage.output_tokens = 900
response._request_id         = "req_example123"
```

`output_text` must contain the **complete** object specified by `referral_extraction_v1.json`; the shortened line above is only a shape example. The backend does not log prompts, transcripts, response text, patient fields, or exception messages. It records the validated object and safe request metadata. `ai_runs` stores model ID, prompt/schema versions, token totals, model cost estimate, status, and `provider_request_id`. `processing_runs` stores the model ID and total estimated OCR-plus-model cost. `aws_request_id` remains an AWS storage/OCR identifier. When an API call times out and usage is unavailable, model cost and total run cost remain unknown rather than being reported as zero.

The default price inputs in `backend/.env.example` are the [official GPT-6.1 Terra standard text rates](https://developers.openai.com/api/docs/models/gpt-6.1-terra) checked November 11, 2026: $1 per million input tokens and $5 per million output tokens. They are configurable estimates, not invoices. A repair call's token usage is added to the first call's usage.

## Failure and data controls

Authentication, permissions, missing model, bad request, and exhausted credits/usage limits are nonretryable until configuration changes. Rate limits, timeouts, connection failures, and server errors are marked retryable. Refusal, invalid JSON, wrong document ID, or exhausted output capacity never becomes an accepted extraction; the referral opens manual review with blank AI suggestions. Provider response bodies and error messages are not written to operational logs. [OpenAI error reference](https://developers.openai.com/api/docs/guides/error-codes)

`store=False` avoids normal Responses API application-state storage. It **does not** disable default abuse-monitoring logs, which OpenAI says may include prompts and responses and are generally retained for up to 30 days. OpenAI says API data is not used for model training by default, but this demo still prohibits real PHI. A real-PHI deployment requires appropriate agreements, account configuration, endpoint review, and a separate security and privacy assessment. [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data)

## Verify with synthetic data

Run the offline backend checks with `uv run pytest -q tests/unit tests/api`; run the PostgreSQL integration suite with `uv run pytest -q tests/integration`. After placing a working project key in `backend/.env`, upload one synthetic referral and confirm the UI reaches **Needs review** with cited suggested fields. Inspect only metadata when diagnosing the run:

```sql
SELECT model_id, status, invocation_count, input_tokens, output_tokens,
       estimated_cost_usd, provider_request_id
FROM ai_runs ORDER BY created_at DESC LIMIT 1;
```

The live OpenAI smoke test requires your local key and was not performed as part of the keyless implementation.
