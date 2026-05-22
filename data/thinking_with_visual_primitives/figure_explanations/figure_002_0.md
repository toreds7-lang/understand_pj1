**What it shows**  
Figure 2 illustrates the model architecture and training pipeline for a multimodal intelligence system based on DeepSeek-V4-Flash. It focuses on the pretraining and post-training stages for developing visual primitive generation capabilities.

**Key components**  
The figure has two main parts: (a) the architecture and (b) the training pipeline. The architecture diagram includes components like DeepSeek-ViT, Text Tokenizer, DeepSeek-V4-Flash, and a De-Tokenizer, showing the flow from images and language instructions to language responses with visual primitives. The training pipeline outlines steps like Pretraining, Specialized SFT (Supervised Fine-Tuning), Specialized RL (Reinforcement Learning), Unified RFT (Reinforced Fine-Tuning), and On-Policy Distillation.

**What the paper concludes from it**  
According to the paper, the model, through its architecture and training processes, achieves foundational capabilities in generating visual primitives during pretraining, which are refined through expert-wise specialization in post-training [page 3]. The design allows effective operation with fewer visual tokens while maintaining cognitive depth [page 3]. The model's integration of visual primitives with language to enhance reasoning capacity stands out as key to its competitive performance across challenging tasks [page 2].

**Caveats / limits**  
The figure does not detail the specific data sets or tasks used in the pretraining and post-training phases, which are crucial for understanding the practical effectiveness of the described processes. Additionally, while it outlines the components, it does not address potential challenges or limitations in combining these multimodal inputs.