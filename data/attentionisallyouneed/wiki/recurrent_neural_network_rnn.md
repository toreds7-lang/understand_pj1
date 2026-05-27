# Recurrent Neural Network (RNN)
_Type: architecture | Papers: attentionisallyouneed_

## Summary
Recurrent Neural Networks (RNNs) are a class of neural networks designed to recognize patterns in sequences of data, such as time series or natural language. They achieve this by maintaining a 'memory' of previous inputs, making them particularly useful for tasks where context is critical. However, RNNs often struggle with long-range dependencies due to difficulties in maintaining context over extended sequences, a limitation that prompted the development of more advanced architectures.

## Where It Appears
- "Attention is All You Need": In this paper, although the primary focus is the attention mechanism, it references RNNs as a foundational architecture for sequence modeling that attention-based models seek to improve upon.

## Related Concepts
- [[long_short_term_memory_lstm]]: LSTMs are a type of RNN designed to overcome the difficulty of learning long-range dependencies.
- [[gated_recurrent_unit_gru]]: GRUs are another RNN variant that simplifies the LSTM architecture while retaining performance for specific tasks.
- [[transformer]]: Transformers are architecturally different and are developed to address RNN's limitation by eliminating the need for sequential processing.

## Key Quotes
> "The model architecture we propose eschews recurrence and instead relies entirely on an attention mechanism to draw global dependencies between input and output." [attentionisallyouneed, p. 1]

## Notes
_Generated: 2026-05-23T12:44:00_