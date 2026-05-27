# Position-wise Feed-Forward Networks
_Type: architecture | Papers: attentionisallyouneed_

## Summary
Position-wise Feed-Forward Networks are a component of the Transformer architecture, introduced in "Attention Is All You Need". These networks apply the same feed-forward network to each position independently and identically. Such a design provides non-linearity to each position after the application of attention, helping to model intricate dependencies within sequence data without relying on recurrence or convolution.

## Where It Appears
- **Attention Is All You Need**: Position-wise Feed-Forward Networks appear as a crucial part of the Transformer model, specifically between the multi-head self-attention mechanism and layer normalization steps. This ensures that each position within the sequence can learn a non-linear representation, crucial for maintaining and enhancing positional independence ([section: "Model Architecture"]).

## Related Concepts
- [[transformer]]: The Position-wise Feed-Forward Network is an integral part of the Transformer architecture, providing non-linear transformations at each position to counterbalance the linearity of attention operations.

## Key Quotes
> "The position-wise feed-forward network consists of two linear transformations with a ReLU activation in between." [attentionisallyouneed, Section: "Model Architecture"]

## Notes
_Generated: 2026-05-23T12:43:45_