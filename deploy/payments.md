# Credit card payments

Pricing is 100 credits per US dollar. Checkout offers $5 / 500 credits,
$10 / 1,000 credits, and $20 / 2,000 credits. The server owns the price and
account association. Stripe Managed Payments collects payment, calculates tax,
and handles its merchant-of-record responsibilities. Applicable tax is added
to the pack price; the credit amount is unchanged. Stripe may offer additional
payment methods and local-currency presentation.

Render quotes use speech character count × estimated processing cost × 2.
Character casting applies a further 50% premium. Round up to a whole credit.
`KENKUI_ESTIMATED_COST_CENTS_PER_MILLION=126` is the initial estimate, calibrated
to the reported $1.50 cost for 1,189,736 Dune characters. That quotes 300 credits
($3) for a narrator and 450 credits ($4.50) for character casting. This is a
length-based estimate, not a measurement of actual compute, storage, model,
or payment processing fees. Recalibrate with representative completed jobs.
The displayed quote and admission reservation use the same calculation.

## Configure and rehearse

1. Keep test/live keys outside the repository, in
   `~/.config/kenkui/deploy/stripe.env` with permissions 0600:
   `STRIPE_TEST_SECRET_KEY` and `STRIPE_LIVE_SECRET_KEY`. Do not put them in chat.
2. Set the API service's `STRIPE_SECRET_KEY` from the matching environment key.
   Never expose it to the web build. `KENKUI_WEB_ORIGIN` supplies the trusted
   HTTPS checkout return origin; it must point at the matching web deployment.
3. Register `https://api.staging.kenkui.fm/v1/billing/webhooks/stripe` in the test
   environment for `checkout.session.completed` and
   `checkout.session.async_payment_succeeded`. Set the returned endpoint signing
   secret as `STRIPE_WEBHOOK_SECRET` on that API service. A CLI listener secret
   is different from a deployed endpoint secret.
4. Deploy server and studio together. Sign in, buy a test pack through Billing,
   finish Stripe test checkout, and verify the balance increases exactly once.
   Replay the delivery and verify it does not increase again. Also rehearse
   declined cards, cancellation, and delayed webhook delivery. Redirecting to
   the success URL alone never grants credits.
5. Check the Stripe account's `charges_enabled` and live card capability before
   enabling live purchases. Register the production URL using live credentials,
   set its own signing secret, then rehearse a real authorized purchase.

Checkout stays unavailable without configuration. A secret API key with no
webhook signing secret prevents startup, so an incomplete configuration cannot
accept payments without fulfillment. Existing balances and settled charges are
preserved; failed/cancelled render reservations continue to be released.

Webhook fulfillment requires a signed delivery within five minutes, a paid
payment-mode USD session, matching USD subtotal/account and a total consistent with tax, and Kenkui purchase metadata.
It deduplicates on Checkout Session ID, including distinct events for the same
purchase. Refunds and disputes require an operator ledger adjustment; automatic
reversal is not implemented in this release.

Reference: https://docs.stripe.com/checkout/fulfillment

## Managed Payments configuration

Checkout explicitly sets `managed_payments[enabled]=true` and omits the
unsupported card-only `payment_method_types` option. Prices use exclusive tax.
`KENKUI_STRIPE_TAX_CODE` defaults to `txcd_10105001` (cloud-based AI services,
personal use), reflecting the hosted rendering service. Confirm the category
for your actual customer offering before launch; a business-focused offering
may require a different tax code. Use a supported Stripe API/webhook version
from 2025-03-31.basil onward so adaptive pricing preserves the USD subtotal.

The CLI OAuth session can administer authorized accounts, but its expiring
access/refresh tokens are not the app's deployable secret API keys. Save app
keys in the private environment file; do not copy CLI OAuth tokens into app
configuration.

## Verification on 2026-09-15

- CLI authorized for Kenkui Team live and test contexts plus Kenkui Team sandbox.
- Sandbox Managed Payments accepted the actual server checkout form for a
  500-credit, $5 purchase. The unpaid validation session was then expired.
- Registered sandbox staging webhook `we_1UG1yo4ErlSRMwOnhbrpzy29`; its signing
  secret is stored in the private environment file as `STRIPE_TEST_WEBHOOK_SECRET`.
- Live account reported charges/payouts disabled, card payments inactive, and
  outstanding `business_profile.url` and `external_account` requirements.
- Completed a sandbox purchase with Stripe test card 4242: $5.00 subtotal,
  $0.53 tax, $5.53 total. Stripe delivered `checkout.session.completed` and
  the real webhook handler returned 204; the isolated test ledger rose from
  0 to 500 credits. Replaying the same signed delivery returned 204 and kept
  the balance at 500. Event: `evt_1UG24K4ErlSRMwOn8RvjmAxM`.
- This rehearsal used the real billing routes and checkout form, CLI OAuth
  for API authentication, a local CLI webhook forwarder, and an in-memory
  account fixture. It does not verify deployed PostgreSQL or deployed checkout.
- Work remains sandbox-only at the owner's request. No live purchase was made.
  App sandbox API keys and deployment configuration remain outstanding.

References:
- https://docs.stripe.com/payments/managed-payments/update-checkout
- https://docs.stripe.com/payments/managed-payments/eligibility
