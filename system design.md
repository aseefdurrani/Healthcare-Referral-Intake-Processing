# System design — Healthcare Referral Intake Copilot

This document expands the [conceptual integration flow in README Section 5](README.md#5-conceptual-integration-flow). It separates the **implemented synthetic-data demo** from a **possible full application**. The diagrams show logical data flow, not a finalized deployment or network design.

| | Implemented demo | Possible full application |
|:--|:--|:--|
| Referral source | Authorized staff upload a synthetic PDF, PNG, or JPEG | Referring clinics could send referrals through an external electronic health record (EHR) system |
| Processing | S3, Textract, FastAPI validation, and OpenAI Responses API | The same intake and review pattern could use an authorized Amazon Bedrock model |
| Data store | Local PostgreSQL | Amazon RDS for PostgreSQL, encrypted at rest with AWS KMS `aws/rds` |
| Visibility | Application logs and review history in the demo | CloudWatch for operational logs/metrics and CloudTrail for configured AWS API activity |
| Outcome | Staff edit and approve or reject **inside the demo** | After staff approval, a separate integration could use a specialty directory to send the referral to the appropriate queue |

## 1. Implemented demo flow

```mermaid
flowchart LR
    Staff[Authorized staff] -->|upload synthetic PDF or image| UI[Next.js<br/>intake and review]
    UI -->|authenticated requests| API[FastAPI<br/>workflow and validation]

    Cognito[Cognito<br/>staff sign-in] -.->|sign-in| UI
    Cognito -.->|JWKS for token validation| API

    API -->|store original| S3[(Private S3<br/>SSE-S3)]
    API <-->|OCR from stored document| Textract[Textract OCR]
    API <-->|fields and specialty suggestion| OpenAI[OpenAI Responses API]
    API <-->|referrals and review history| LocalDB[(Local PostgreSQL)]

    API -->|suggestions and flags| UI
    UI -->|staff corrections and decision| API

    classDef person fill:#f1f5f9,stroke:#64748b,color:#0f172a;
    classDef app fill:#eff6ff,stroke:#2563eb,color:#0f172a;
    classDef service fill:#ecfdf5,stroke:#059669,color:#064e3b;
    classDef data fill:#f8fafc,stroke:#475569,color:#0f172a;
    class Staff person;
    class UI,API app;
    class Cognito,Textract,OpenAI service;
    class S3,LocalDB data;
```

FastAPI coordinates storage, OCR, AI interpretation, validation, and persistence; the service arrows above are logical exchanges through the API. The suggestion is reviewed by a person before the demo records **approved** or **rejected**. The demo has no EHR ingestion or outbound specialty routing. S3 uses **SSE-S3 (`AES256`)**, which does not use an application-managed KMS key.

## 2. Possible full application

```mermaid
flowchart LR
    Clinic[Referring clinics] -.->|submit referrals| EHR[External EHR system]
    EHR -.->|secure inbound integration| API[FastAPI<br/>intake, validation, workflow]

    Staff[Authorized staff] --> UI[Next.js<br/>intake and review]
    UI -->|authenticated requests| API
    Cognito[Cognito<br/>staff sign-in] -->|sign-in| UI
    Cognito -->|JWKS for token validation| API

    API -->|store original| S3[(Private S3<br/>SSE-S3)]
    API <-->|OCR from stored document| Textract[Textract OCR]
    API -.->|send OCR transcript| Bedrock[Amazon Bedrock<br/>proposed AI provider]
    Bedrock -.->|fields and specialty suggestion| API
    API -.->|read and write referral records| RDS[(Amazon RDS<br/>PostgreSQL)]
    KMS[AWS KMS<br/>aws/rds] -.->|storage encryption| RDS

    API -->|suggestions and flags| UI
    UI -->|staff corrections and approval| API
    API -.->|approved referral only| Directory[Specialty directory]
    Directory -.->|appropriate destination| Queue[Specialty queue]

    API -.->|operational logs and metrics| Watch[CloudWatch]
    S3 -.->|object data events if configured| Trail[CloudTrail]

    classDef existing fill:#eff6ff,stroke:#2563eb,color:#0f172a;
    classDef proposed fill:#fffbeb,stroke:#d97706,color:#78350f,stroke-dasharray:5 4;
    classDef person fill:#f1f5f9,stroke:#64748b,color:#0f172a;
    class Staff person;
    class UI,API,S3,Textract,Cognito existing;
    class Clinic,EHR,Bedrock,RDS,KMS,Directory,Queue,Watch,Trail proposed;
```

**Reading this diagram:** Blue nodes are capabilities used by the demo; amber dashed-outline nodes and dashed connections are proposed integrations. The entire diagram is a **future design**, not a claim that these services are deployed together. An external EHR would sit before intake and receive referrals from referring organizations; direct staff upload can remain available. After a human approves a referral, a future directory and delivery integration could select and send it to a specialty queue. That transmission does not exist in the demo.

Amazon Bedrock is a **potential replacement** for the demo's OpenAI provider, subject to model authorization and implementation. The Bedrock adapter in the repository is inactive. Amazon RDS and its KMS `aws/rds` encryption, CloudWatch integration, CloudTrail configuration, EHR ingestion, and specialty delivery are **not fully implemented** in the current demo. CloudTrail would record selected AWS activity where configured; it would not replace the application's staff-level review history. S3 remains shown with SSE-S3, so the KMS connection applies to **RDS only**.

Both views preserve the same safety boundary: OCR reads the document, AI proposes fields and a specialty, FastAPI applies deterministic checks, and an authorized person makes the decision. The demo uses synthetic data only; a real-patient deployment would require its own security, privacy, clinical, and integration review.
