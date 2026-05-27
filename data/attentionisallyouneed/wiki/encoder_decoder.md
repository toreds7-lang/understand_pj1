# Encoder-Decoder
_Type: architecture | Papers: attentionisallyouneed_

## Summary
The encoder-decoder is a neural network architecture used primarily in sequence-to-sequence tasks, such as translation, summarization, or text generation. It involves an encoder that processes the input data and converts it into a fixed-sized context or state, which the decoder then uses to produce the output sequence. This architecture is significant because it allows for handling variable-length input and output sequences, making it versatile for a wide range of applications.

## Where It Appears
- **Attention Is All You Need**: This paper, which introduces the Transformer model, enhances the traditional encoder-decoder architecture by incorporating self-attention mechanisms to improve the processing of sequences ([attentionisallyouneed, p. 2]).

## Related Concepts
- [[transformer]]: The Transformer model is an advancement on the encoder-decoder architecture, replacing recurrent layers with self-attention to handle sequences more efficiently.
- [[sequence_to_sequence]]: The encoder-decoder is fundamental to sequence-to-sequence models, providing the basic structure for input-to-output mapping.
- [[self_attention]]: Self-attention mechanisms, as used in Transformers, enhance the encoder-decoder model by allowing it to weigh the importance of different parts of the input.

## Key Quotes
> "The model replaces the earlier Recurrent General Sentence Encoder-Decoder architectures with an attention-based approach, effectively improving upon the sequence handling via encoders and decoders." [attentionisallyouneed, p. 1]

## Notes
_Generated: 2026-05-23T12:44:13_