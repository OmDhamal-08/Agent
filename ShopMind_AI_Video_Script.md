# ShopMind AI — Demo Video Script (6–7 min)

**Format tip:** Record screen + voice (Loom, OBS, or QuickTime). Keep energy up, talk while you click — don't narrate after the fact. Rehearse once before final recording.

---

## 0. Hook (0:00 – 0:15)

> "Most AI shopping bots are just chatbots with a fancy prompt — they hallucinate prices, they can't actually complete a purchase, and merchants have no idea what they're doing. I built ShopMind AI to fix all three problems, for Razorpay's Agentic Commerce track."

**On screen:** Landing page / chat UI, nothing clicked yet.

---

## 1. The Problem, Fast (0:15 – 0:45)

> "Razorpay's brief asks for two things: an agent that grows merchant revenue, AND a merchant catalog that's transactable by *other* AI agents — not just humans clicking a website. Most submissions will nail the first and skip the second. ShopMind does both."

**On screen:** Optionally show a slide/text overlay with the two requirements bolded.

---

## 2. What Makes This a *Real* Agent (0:45 – 1:30)

> "This isn't a scripted recommendation flow. The LLM — Gemini, with a swappable adapter layer — has zero built-in knowledge of my products. Every price, every spec, every stock number comes from a live Postgres query through tool calls. If a tool doesn't return it, the model literally cannot say it."
>
> "And it runs in a real reasoning loop: it decides which tool to call, looks at the result, decides the next step — up to 6 calls per turn. It can even decide to *skip* an upsell if the customer already owns that item. That's a judgment call the model makes, not a hardcoded rule."

**On screen:** Briefly show `tools.py` / `agent_loop.py` in the editor — just enough to prove it's real code, not smoke and mirrors. 3-4 seconds max, don't linger.

---

## 3. Live Demo — Chat Flow (1:30 – 3:30)

> "Let's watch it work."

**Do this on screen, narrating each step:**
1. Type a realistic query: *"I need a laptop for coding under ₹70,000"*
2. Point out the agent calling `search_products` — show the tool call happening (console/network tab or a debug panel if you have one).
3. Agent responds with 2-3 real options pulled from Postgres.
4. Ask a follow-up: *"Compare the top two"* → agent calls `compare_products`.
5. Say: *"Now watch — this is the part that matters. The agent wants to add this to cart, but it CANNOT do it on its own."*
6. Show the **confirm button** appear. Click it.
7. Point out: *"That confirmation is a hard gate — `confirmed=true` is required server-side. No amount of clever prompting from a user can trigger a cart-add or a payment without this explicit click."*
8. Continue to checkout, trigger Razorpay Checkout (test mode).

**Voiceover during this section:**
> "Every one of these steps — the search, the comparison, the cart add, the checkout — is logged with the tool name, input, output, and the agent's own reasoning for why it acted. That log is what powers the merchant dashboard I'll show you next."

---

## 4. Graceful Failure Handling (3:30 – 4:30)

> "Judges specifically care about this: does the system handle failure, or does it just work in the happy path and fall apart otherwise? Let me show you a real failure."

**Do this on screen:**
1. Trigger your failure scenario — either:
   - **Payment failure:** use a Razorpay test-mode failure card, or
   - **Stock-out:** flip a product's stock to 0 mid-conversation.
2. Show the agent's response — a clear, human message, not a raw error.
3. Switch to the dashboard and show this event in the **AI Decision Trace**, marked `success=false` with a failure reason.

> "Nothing is hidden. The order — or the failed action — shows up distinctly in the dashboard, exactly as it happened."

---

## 5. The Merchant Dashboard (4:30 – 5:30)

> "Now, from the merchant's side."

**Show, narrating each panel:**
- **Business Impact panel** — revenue influenced by the agent, upsell acceptance rate.
- **AI Decision Trace** — the full audit log: every tool call, every decision, every approval.
- **AI vs Human comparison table** — what the agent recommended vs. what was actually purchased.
- **The failure event** you just triggered, visible here too.

> "This is the audit trail Razorpay's brief explicitly asks for — every money action is explainable, bounded, and gated. Nothing the agent does is a black box."

---

## 6. The Second Track Requirement — Agent-Readable Catalog (5:30 – 6:15)

> "Here's the part most people will skip: making the merchant transactable by *other* AI agents, not just our own chat UI."

**On screen:** Hit `GET /api/agent-catalog` directly in the browser or Postman, show the raw JSON response.

> "This endpoint exposes the full catalog — id, price, specs, stock, category — in a clean, predictable schema any external AI agent or system could query programmatically. It's a small, honest example of machine-readable commerce, not a full agent-to-agent protocol, but it directly proves the merchant's catalog is discoverable beyond our own frontend."

---

## 7. Close (6:15 – 6:45)

> "So to recap: a real reasoning agent with zero hallucinated facts, hard confirmation gates on every money action, a full audit trail, one gracefully handled failure, and a catalog that's queryable by other AI systems — not just humans. That's ShopMind AI, built for Razorpay's Agentic Commerce track."

**On screen:** End on the dashboard or a simple thank-you/GitHub-link slide.

---

## Recording Checklist
- [ ] Have test data seeded (25 laptops) before recording — don't seed live on camera
- [ ] Pre-trigger the failure scenario once in rehearsal so you know exactly what it looks like
- [ ] Have Razorpay test-mode card numbers handy (success + failure)
- [ ] Keep code shots brief — this is a product demo, not a code walkthrough
- [ ] Time yourself once beforehand; trim the demo section first if you're running long
