# Anfinity Frontend

Anfinity is an AI knowledge operating system for teams that need to capture, organize, search, understand, and approve knowledge across notes, documents, and connected tools.

Deployment is documented in [../DEPLOYMENT.md](../DEPLOYMENT.md). The production
frontend is intended to run on Vercel from this `Frontend/` directory, with
PartyKit deployed separately for collaboration rooms.

The product is positioned around one sharp workflow:

1. Create or join a workspace.
2. Add notes and upload documents.
3. Search by meaning.
4. Ask grounded questions across workspace knowledge.
5. Review answers with citations, source cards, highlights, and feedback.
6. Collaborate and approve important knowledge with workspace permissions.

## Business Model

The frontend consumes the backend billing catalog at `/billing/plans` so pricing, plan comparison, and entitlement prompts stay aligned with server-side enforcement.

Current packaging:

| Plan | Price | Intended customer |
| --- | --- | --- |
| Free | $0 | Solo testers and early adopters |
| Pro | $12/month | Solo power users, researchers, founders |
| Team | $18/user/month | Startups, agencies, product teams, research teams |
| Business | $29/user/month | Growing teams that need governance and admin controls |
| Enterprise | Custom | Larger organizations with SSO, compliance, and private deployment needs |

Usage add-ons are displayed as expansion packaging only; self-serve checkout should appear after matching Stripe prices and backend fulfillment are configured.

## Development

```bash
npm install
npm run dev
```

## Verification

```bash
npm run test:unit
npm run build
```
