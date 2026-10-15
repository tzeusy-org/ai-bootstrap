## Purpose
Keep worker requests bounded and observable while preserving each caller's isolation throughout execution.

## ADDED Requirements

### Requirement: Bounded Worker Requests
The worker SHALL finish each admitted request within its configured execution budget.

ID: REQ-core-worker-001
Source: RFC 0004 §1.2
Scope: v1-mandatory

#### Scenario: A request stays bounded
- **WHEN** a worker admits a request
- **THEN** the configured execution budget applies
