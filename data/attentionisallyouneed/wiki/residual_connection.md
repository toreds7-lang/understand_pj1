# Residual Connection
_Type: mechanism | Papers: attentionisallyouneed_

## Summary
Residual connections are a mechanism used in deep neural networks to allow gradients to flow more effectively during backpropagation. This works by adding the input of a layer to its output, which helps prevent the vanishing gradient problem and enables the training of very deep networks. They are critical in Transformer architectures, enabling the network to learn robust representations without sacrificing depth.

## Where It Appears
- **Attention Is All You Need**: Residual connections are integral to the architecture presented in this paper, enhancing both training dynamics and the network's ability to learn complex patterns without degradation through deeper layers.

## Related Concepts
- [[transformer]]: Residual connections are a core component of the Transformer architecture, allowing it to maintain efficient training and high performance as model depth increases.

## Key Quotes
> "Residual connections are added around each sub-layer, followed by layer normalization." [attentionisallyouneed, p. 4]

## Notes
_Generated: 2026-05-23T12:43:39_