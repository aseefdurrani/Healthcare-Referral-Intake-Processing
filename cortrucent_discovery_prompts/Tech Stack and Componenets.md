# Cortrucent — AI Healthcare Referral Intake Copilot

## MVP Technology Stack & Components

**Project:** AI Healthcare Referral Intake Copilot  
**Scope:** MVP / Proof of Concept  
**Data:** Synthetic patient and referral information only

### A. Frontend

**Technology:** Next.js

**Purpose:**

- Provide a web-based dashboard for healthcare staff.
- Allow staff to upload and view referral documents.
- Display extracted patient information, suggested departments, and referral statuses.
- Highlight missing fields, inconsistencies, and referrals requiring review.
- Allow staff to review and approve processed referrals.

### B. Backend

**Technology:** Python + FastAPI

**Purpose:**

- Serve as the central application API and processing orchestration layer.
- Coordinate document storage, OCR, AI processing, and database operations.
- Validate extracted information using deterministic rules.
- Manage referral statuses and processing workflows.
- Handle authenticated API requests and enforce application access controls.
- Record processing events and errors.

### C. Document Storage

**Technology:** Amazon S3

**Purpose:**

- Securely store original referral PDFs, fax images, and uploaded documents.
- Encrypt stored documents using AWS-managed encryption.
- Associate documents with their corresponding referral records.
- Restrict document access to authorized application services and staff.

### D. Database

**Technology:** PostgreSQL + Amazon RDS

**Purpose:**

- Store referral records and patient information extracted from documents.
- Store structured fields, suggested departments, and referral statuses.
- Track validation results, missing information, and staff review decisions.
- Maintain relationships between referrals, documents, and processing results.

### E. Optical Character Recognition (OCR)

**Technology:** Amazon Textract

**Purpose:**

- Read text from uploaded PDFs and scanned referral documents.
- Extract machine-readable text and document layout information.
- Identify content within forms and tables where applicable.
- Provide the extracted document content to the AI processing layer.

### F. AI Processing

**Technology:** Amazon Bedrock

**Purpose:**

- Interpret the text provided by the OCR layer.
- Extract and organize relevant information into structured fields.
- Identify patient details, referral reasons, and other relevant information.
- Propose the appropriate medical specialty or department.
- Identify potentially missing or ambiguous information.
- Return structured results for backend validation and staff review.

### G. Authentication & Access Control

**Technology:** Amazon Cognito

**Purpose:**

- Provide secure email-and-password login for healthcare staff.
- Authenticate users before granting application access.
- Protect application endpoints and restrict unauthorized access.

**MVP Configuration:**

- One pre-created staff account for the Cortrucent demonstration.
- Email-and-password authentication only.
- No public registration or additional staff roles.
- Backend authorization ensures only the designated staff account can access referral data.

### H. Encryption Key Management

**Technology:** AWS Key Management Service (KMS)

**Purpose:**

- Manage encryption keys used by supported AWS storage services.
- Support encryption of sensitive information stored within AWS infrastructure.
- Integrate with services such as Amazon RDS and, when configured, Amazon S3.

**MVP Configuration:**

- Standard AWS-managed encryption configurations.
- No custom encryption algorithms or manual key-management workflows.
- Amazon S3 default encryption and Amazon RDS encryption enabled.

### I. Monitoring & Audit Logging

**Technology:** Amazon CloudWatch + AWS CloudTrail

**Purpose:**

- Monitor application performance, processing latency, and operational errors.
- Track OCR, AI processing, and backend workflow failures.
- Monitor AWS infrastructure and service health.
- Audit supported AWS API activity and administrative operations.
- Support troubleshooting and security investigations.

**MVP Configuration:**

- Basic application and processing logs.
- Error and performance monitoring.
- AWS activity auditing.
- No sensitive patient details or raw referral contents in operational logs.

---

## MVP End-to-End Workflow

1. An authorized staff member logs into the application.
2. The staff member uploads a referral PDF or scanned document.
3. The original document is securely stored in Amazon S3.
4. Amazon Textract reads the document and produces machine-readable text.
5. Amazon Bedrock interprets the content, generates structured referral fields, and proposes a specialty classification.
6. The FastAPI backend validates the structured results and flags missing or inconsistent information.
7. PostgreSQL stores the referral record, extracted fields, classification, and processing status.
8. The Next.js dashboard displays the results for staff review and approval.
9. CloudWatch and CloudTrail support operational monitoring and AWS activity auditing.

**MVP Limitations:** The demonstration will use synthetic patient data, one authorized staff account, and human-approved referral routing. It will not autonomously transmit referrals to real healthcare departments or integrate with a production EHR.

**Production Consideration:** A production deployment using real patient PHI would require additional security controls, appropriate Business Associate Agreements, formal risk assessment, and organizational HIPAA compliance measures.
