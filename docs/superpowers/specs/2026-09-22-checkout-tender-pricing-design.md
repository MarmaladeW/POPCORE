# Checkout Tender Pricing Design

## Scope

Keep the five real payment methods: Card, Cash, E-transfer, WeChat Pay, and Alipay. Check is not a POPCORE payment method. E-transfer, WeChat Pay, and Alipay follow the same pricing rule as Cash.

## Pricing rules

- Card only uses the exact original total.
- Any payment made only with Cash, E-transfer, WeChat Pay, or Alipay uses the existing cash-discount suggestion, including combinations of those methods.
- Any split that includes Card and another supported method gets no cash discount. Before the first payment, floor the original total to whole dollars when it is at least CA$10.00.
- A Card-inclusive split below CA$10.00 keeps the exact total. Examples: CA$7.90 stays CA$7.90, CA$28.31 becomes CA$28.00, and CA$31.91 becomes CA$31.00.

## Checkout interaction

The cashier selects Split payment and then confirms “This split includes Card” before taking the first payment. This fixes the pricing target before either tender is collected, so Card or the other method may be paid first. POPCORE continues to treat Clover's confirmed total as authoritative and never records a suggested target as received money.

No Clover configuration, payment processing, inventory behavior, or backend payment facts change in this work.
