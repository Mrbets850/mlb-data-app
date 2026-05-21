# The MLB Edge — Landing Page

Production-ready landing page for [themlbedge.com](https://themlbedge.com) with Stripe Checkout integration for subscription billing ($4.99/month with a 3-day free trial).

## Stack

- **Next.js 16** (App Router)
- **Tailwind CSS v4**
- **Stripe** (Checkout Sessions + Webhooks)
- **TypeScript**

## Project Structure

```
landing/
├── app/
│   ├── layout.tsx                          # Root layout, SEO meta, fonts
│   ├── page.tsx                            # Landing page (all sections)
│   ├── globals.css                         # Tailwind + brand design tokens
│   ├── success/
│   │   └── page.tsx                        # Post-checkout success page
│   ├── components/
│   │   ├── Header.tsx                      # Fixed nav with scroll behavior
│   │   ├── CheckoutButton.tsx              # CTA → Stripe Checkout redirect
│   │   └── FAQ.tsx                         # Accordion FAQ section
│   └── api/
│       ├── create-checkout-session/
│       │   └── route.ts                    # POST — creates Stripe Checkout session
│       └── stripe-webhook/
│           └── route.ts                    # POST — handles Stripe webhook events
├── public/
│   └── logo.jpeg                           # Brand logo
├── .env.example                            # Required environment variables
├── package.json
└── README.md
```

## Setup

### 1. Install dependencies

```bash
cd landing
npm install
```

### 2. Configure environment variables

Copy `.env.example` to `.env.local` and fill in your Stripe credentials:

```bash
cp .env.example .env.local
```

You'll need:

| Variable | Where to get it |
|---|---|
| `STRIPE_SECRET_KEY` | [Stripe Dashboard → API Keys](https://dashboard.stripe.com/apikeys) |
| `STRIPE_PRICE_ID` | Create a recurring product ($4.99/month) in [Stripe Dashboard → Products](https://dashboard.stripe.com/products), then copy the Price ID |
| `STRIPE_WEBHOOK_SECRET` | [Stripe Dashboard → Webhooks](https://dashboard.stripe.com/webhooks) or via `stripe listen` CLI |
| `NEXT_PUBLIC_APP_URL` | Your deployment URL (e.g. `https://themlbedge.com`) |

### 3. Create the Stripe Product

In the Stripe Dashboard:

1. Go to **Products** → **Add Product**
2. Name: `The MLB Edge`
3. Pricing: **Recurring**, $4.99/month
4. Save and copy the **Price ID** (starts with `price_`)
5. Paste it as `STRIPE_PRICE_ID` in your `.env.local`

The 3-day free trial is configured automatically in the checkout session code — you don't need to set it on the Stripe product.

### 4. Run locally

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

### 5. Test Stripe locally

Install the [Stripe CLI](https://stripe.com/docs/stripe-cli) and forward webhooks:

```bash
stripe listen --forward-to localhost:3000/api/stripe-webhook
```

Copy the webhook signing secret it outputs and set it as `STRIPE_WEBHOOK_SECRET`.

Use Stripe test card `4242 4242 4242 4242` with any future expiry and any CVC.

## Webhook Events Handled

| Event | Purpose |
|---|---|
| `checkout.session.completed` | Provision access after successful checkout |
| `customer.subscription.created` | Track new subscriptions (starts in `trialing` status) |
| `customer.subscription.updated` | Handle trial → active transitions and status changes |
| `customer.subscription.deleted` | Revoke access on cancellation |

The webhook handler logs events to the console. Add your database/auth logic in the marked `TODO` sections.

## Deployment

This is a standard Next.js app. Deploy to:

- **Vercel**: Connect the repo and set environment variables in the dashboard
- **Railway**: Use the Next.js buildpack
- **Any Node.js host**: `npm run build && npm start`

Set `NEXT_PUBLIC_APP_URL` to your production domain (e.g. `https://themlbedge.com`).

### Stripe Webhook in Production

1. Go to [Stripe Dashboard → Webhooks](https://dashboard.stripe.com/webhooks)
2. Add endpoint: `https://yourdomain.com/api/stripe-webhook`
3. Select events: `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`
4. Copy the signing secret → set as `STRIPE_WEBHOOK_SECRET`

## Subscription Flow

1. User clicks **Start Free Trial** on the landing page
2. Frontend calls `POST /api/create-checkout-session`
3. Backend creates a Stripe Checkout session with `mode: "subscription"` and `trial_period_days: 3`
4. User is redirected to Stripe Checkout
5. After completing checkout, user lands on `/success`
6. Stripe fires webhook events for subscription lifecycle management
