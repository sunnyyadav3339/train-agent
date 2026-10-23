---
name: httpbin-order-form
description: Fill and submit the pizza order test form at httpbin.org/forms/post and read back the echoed order.
triggers: [httpbin, order form, forms/post]
params: [name, phone, size, topping]
---

## Goal
The form at https://httpbin.org/forms/post is submitted with the given customer name, phone,
size (Small / Medium / Large) and one topping (Bacon / Extra Cheese / Onion / Mushroom).
httpbin is a test service: it only echoes the submitted fields back as JSON.

## Fast path
Run `run_skill_steps` with `name`, `phone`, `size`, `topping`. It ends on the result page
(URL ends in /post).

## Verify
On the result page, `read_page` shows a "form" object whose custname, custtel, size and
topping equal what was asked (size and topping are lower-case there).

## Known failures and recovery
- httpbin sometimes answers 502/503: `browser_back` and submit again.
- If a field kept old text, type into it again with `clear: true`.

## When to ask a human
Never needed.
