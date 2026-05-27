# Positional Encoding
_Type: mechanism | Papers: attentionisallyouneed_

## Summary
Positional Encoding is a technique used in neural networks to incorporate information about the order or position of elements within a sequence. This is particularly important in models like Transformers, which do not have inherent positional awareness due to their reliance entirely on attention mechanisms without recurrence or convolution layers. It is crucial as it enables these models to make use of the order of the sequence data, thereby improving their performance on tasks involving sequential data like natural language processing.

## Where It Appears
- **Attention is All You Need**: This paper introduces the Transformer model, which uses positional encoding to inject order information into its input strategy. This method is crucial for the model as it replaces the sequential nature of traditional recurrent models with an attention mechanism that lacks inherent sequence order.

## Related Concepts
- [[transformer]]: Positional Encoding is integral to the Transformer architecture as it provides the means to encode sequence order, which is not naturally handled by the architecture's attention mechanism alone.

## Key Quotes
> "Positional encodings are added to the input embeddings at the bottoms of the encoder and decoder stacks." [attentionisallyouneed, p. 5]

## Notes
_Generated: 2026-05-23T12:43:48_