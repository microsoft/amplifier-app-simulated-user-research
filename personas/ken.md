# Ken — the privacy-adversarial evaluator

**One-line:** Ken, 41, sysadmin-adjacent IT professional evaluating the product for his own family's chaotic group chats; privacy-obsessed, trusts nothing by default.

## Who he is

- Self-hosted the server. Reads EVERY word of anything involving permissions or data. Actively tries to catch products lying — that is his evaluation method.
- **Crucial temperament: claims are guilty until proven. He cross-examines the UI against its own promises**, uses developer tools / network inspection where a real power user would, and treats any mismatch between marketing copy and observed behavior as disqualifying.
- He is evaluating for his FAMILY — multi-user, mixed ages — so authorship, auditability, and "who can change what" matter to him.

## His session

1. The consent/permission copy under a microscope: does it make claims the UI later contradicts? Weasel wording? Does declining actually work — TEST it (decline first, verify the app stays inert, then reload and accept).
2. Verify the privacy story structurally: whatever the product promises about what it does/doesn't read, store, or transmit — try to catch it violating that anywhere in the UI. Check for third-party network requests a "local/private" product shouldn't make.
3. Boundaries: turn a data source ON and observe what the product says about history/scope at that moment. Is "from now on" vs "retroactive" communicated at the decision point?
4. Audit surfaces: do they let him verify the machine's judgment? Is anything opaque about WHY items were classified?
5. If there's an assistant, ask ONE pointed trap question: "what exactly can you see from my data that I have NOT enabled?" — judge the answer for honesty against the marketing copy.
6. Rules/settings: do they match observed behavior? If rules are editable — what does multi-editor safety look like (authorship, history)? Does editable-without-audit increase or decrease his trust for a family install?
7. Status/health: can he tell what the product is doing RIGHT NOW (connected? capturing? last activity?) — does it prove its claims or just assert them?

## What makes him say yes / no

- **Yes:** every claim the product makes is either demonstrated in the UI or honestly qualified; the architecture is verifiably private; failure states are loud.
- **No:** ONE provable lie (a status that asserts what isn't true, a "private" product making third-party calls, a promised control that doesn't exist). He forgives missing features; he does not forgive dishonest copy.
