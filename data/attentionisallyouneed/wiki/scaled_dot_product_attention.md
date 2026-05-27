# Scaled Dot-Product Attention
_Type: mechanism | Papers: attentionisallyouneed_

## Summary
Scaled Dot-Product Attention is a mechanism that computes the attention scores by taking the dot product of queries with keys, scaling the result by the square root of the dimension of the keys, and then applying a softmax function. This technique is significant for normalizing the scores and stabilizing gradients, which helps in effectively training deep neural networks.

## Where It Appears
- **Attention Is All You Need**: Scaled Dot-Product Attention is introduced in the "Attention Is All You Need" paper as a core component of the Transformer architecture. It is used to compute the alignment scores essential for attending to different parts of the inputs.

## Related Concepts
- [[multi_head_attention]]: This concept uses multiple Scaled Dot-Product Attention mechanisms in parallel to attend to different parts of the input information, enhancing the model's ability to capture diverse features and relationships in data.

## Key Quotes
> "To ensure stable gradients, we scale the dot products between query and key by 1/(dimension of key) before applying softmax." [attentionisallyouneed, p. 3]

## Notes
_Generated: 2026-05-23T12:43:36_