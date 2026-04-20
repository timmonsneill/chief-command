---
name: Infrastructure Plan
description: Everything runs on Google Cloud. Railway is DEAD. Google Cloud for dev AND production (Cloud Run, Cloud SQL, GCS, Vertex AI) for HIPAA compliance.
type: project
---

# Infrastructure — Google Cloud Only

**Railway is DEAD. Do not reference it. Everything runs on Google Cloud.**

Updated 2026-04-16: Owner confirmed Railway is completely gone. Google Cloud is both dev and production.

## Stack
- **Compute:** Google Cloud Run
- **Database:** Cloud SQL for PostgreSQL
- **File Storage:** Google Cloud Storage (GCS)
- **AI:** Vertex AI (for Archie / AI Brain features)
- **Auth:** JWT-based
- **Frontend dashboard:** Netlify (static build of Arch dashboard only)

## Why Google Cloud
- HIPAA compliance via BAA (Business Associate Agreement)
- Vertex AI stays within HIPAA-covered environment
- Cloud SQL gives enterprise-grade backup/recovery
- All data stays within Google's infrastructure

## Separate deployments
- **Arch EMR:** Google Cloud (Cloud Run + Cloud SQL)
- **Arch Dashboard:** Netlify (static frontend, archdashboard.netlify.app)
- **Chief Command:** Owner's Mac via Cloudflare tunnel (chiefcommand.app)
