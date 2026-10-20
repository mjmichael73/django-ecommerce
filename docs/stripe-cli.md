# Stripe CLI — local webhook forwarding

## Overview

The [Stripe CLI](https://docs.stripe.com/stripe-cli) is the tool for exercising Stripe
webhooks **locally, end-to-end, without deploying anything public**.

Two commands matter here:

- `stripe listen` starts a tunnel that receives real Stripe events and forwards them
  to a local HTTP endpoint. It prints a **per-run signing secret** (`whsec_...`) — that
  secret is different on every run.
- `stripe trigger` fires a synthetic event (a "card-shaped" event, e.g.
  `checkout.session.completed`) through the tunnel, so you can drive the webhook
  handler on demand.

The webhook endpoint in this project is **`/payment/webhook/`** (`payment/urls.py`), and
the Compose stack publishes the web service on **port 8089** (see the `web` service
`ports` mapping in `docker-compose.yml`). The forward target is therefore:

```
localhost:8089/payment/webhook/
```

The signature check in `payment/webhook/` reads the `STRIPE_WEBHOOK_SECRET` environment
variable (`be/ecommerce/ecommerce/settings.py`). If the secret does not match the one the
running `stripe listen` printed, **every** delivery is rejected with HTTP 400.

## Quick start

1. Start the forwarding tunnel (leave it running):

   ```bash
   stripe listen --forward-to localhost:8089/payment/webhook/
   ```

2. Copy the printed `whsec_...` into your `.env` as the webhook secret:

   ```bash
   STRIPE_WEBHOOK_SECRET=whsec_...
   ```

   The webhook signature check in `payment/webhook/` reads this variable; a wrong or
   stale secret means every delivery 400s.

3. Restart the web service so the new environment variable is picked up:

   ```bash
   docker compose up -d web
   ```

   (Only the web service needs restarting — Postgres, Redis and RabbitMQ keep running.)

4. Fire a test event through the tunnel:

   ```bash
   stripe trigger checkout.session.completed
   ```

   For an order that actually exists in your database, the order flips to **paid** in
   the admin and the invoice email task runs.

## Caveats

- **`stripe trigger` uses a synthetic session.** Its `client_reference_id` does not
  match any real order, so the webhook's **400 response is the correct outcome** for it.
  A 400 here means "no matching order", not "broken setup". To drive a real order
  end-to-end, use the checkout UI with test keys and the Stripe test card
  `4242 4242 4242 4242` — the Checkout Session it creates carries the real
  `client_reference_id` (the order id) and the webhook flips that order to paid.
- **The webhook is idempotent** (card 7e732hbh): Stripe delivers webhooks at-least-once,
  and replays never double-invoice. That makes retries safe to exercise — you can
  replay the same event and watch the invoice task *not* fire a second time.
- **`charge.refunded` events arrive through the same tunnel.** The same
  `stripe listen` session forwards refund events, and the webhook handles inbound
  `charge.refunded` events (card a3cg42c4) by recording the refunded amount on the
  matching order. Refunds are never issued automatically — they are analyst-initiated
  (see the admin action) or observed from inbound events.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| 400 responses, and `whsec_` never appears in the logs | `STRIPE_WEBHOOK_SECRET` is not set in the environment the web service runs with | Put `STRIPE_WEBHOOK_SECRET=whsec_...` in `.env` and `docker compose up -d web` |
| 400 on **every** event, including ones you just triggered | Stale or rotated secret — the `stripe listen` session restarted and printed a new `whsec_...` | Copy the new secret into `.env` and restart the web service |
| No events arriving at all | The forward URL is not reachable from the host | Confirm routing with `curl -I localhost:8089/payment/webhook/` — it must return **400** (not 404); a 400 proves the URL is routed to the webhook view, and any non-404 answer means the tunnel should be able to reach it |

## See also

- [README — Stripe (payments & webhooks)](../README.md#4-stripe-payments--webhooks)
- [Production deployment](production.md) — the real webhook endpoint setup
- [Security](security.md) — signature verification and key handling
