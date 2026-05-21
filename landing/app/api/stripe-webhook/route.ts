import { NextRequest, NextResponse } from "next/server";
import Stripe from "stripe";

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!, {
  typescript: true,
});

const webhookSecret = process.env.STRIPE_WEBHOOK_SECRET!;

export async function POST(request: NextRequest) {
  const body = await request.text();
  const signature = request.headers.get("stripe-signature");

  if (!signature) {
    return NextResponse.json(
      { error: "Missing stripe-signature header." },
      { status: 400 }
    );
  }

  let event: Stripe.Event;

  try {
    event = stripe.webhooks.constructEvent(body, signature, webhookSecret);
  } catch (err) {
    const message =
      err instanceof Error ? err.message : "Webhook signature verification failed.";
    console.error("Webhook signature verification failed:", message);
    return NextResponse.json({ error: message }, { status: 400 });
  }

  switch (event.type) {
    case "checkout.session.completed": {
      const session = event.data.object as Stripe.Checkout.Session;
      const subscriptionId = session.subscription as string | null;
      console.log(
        `Checkout completed: customer=${session.customer}, subscription=${subscriptionId}`
      );

      // Auto-cancel after the first payment so it acts as a one-time charge.
      // The customer keeps access through the end of the paid period.
      if (subscriptionId) {
        try {
          await stripe.subscriptions.update(subscriptionId, {
            cancel_at_period_end: true,
          });
          console.log(`Subscription ${subscriptionId} set to cancel at period end`);
        } catch (err) {
          console.error("Failed to set cancel_at_period_end:", err);
        }
      }

      // TODO: Provision access for the customer.
      // Look up or create your user record using session.customer_email or session.customer,
      // then grant them access to The MLB Edge app.
      break;
    }

    case "customer.subscription.created": {
      const subscription = event.data.object as Stripe.Subscription;
      console.log(
        `Subscription created: id=${subscription.id}, status=${subscription.status}, customer=${subscription.customer}`
      );
      // Status will be "trialing" during the 3-day free trial.
      break;
    }

    case "customer.subscription.updated": {
      const subscription = event.data.object as Stripe.Subscription;
      console.log(
        `Subscription updated: id=${subscription.id}, status=${subscription.status}, cancel_at_period_end=${subscription.cancel_at_period_end}`
      );
      // After trial ends the status moves to "active" and the $4.99 charge fires.
      // cancel_at_period_end will be true (set by checkout.session.completed handler above).
      break;
    }

    case "customer.subscription.deleted": {
      const subscription = event.data.object as Stripe.Subscription;
      console.log(
        `Subscription ended: id=${subscription.id}, customer=${subscription.customer}`
      );
      // The single billing cycle is complete. No further charges will occur.
      break;
    }

    default:
      console.log(`Unhandled event type: ${event.type}`);
  }

  return NextResponse.json({ received: true });
}
