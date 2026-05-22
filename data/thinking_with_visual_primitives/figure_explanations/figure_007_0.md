**What it shows**  
Figure 3 presents illustrative examples of cold-start data used for coarse-grained and fine-grained counting tasks. It demonstrates how a model uses intent decomposition and visual primitives to identify and count entities within visual scenes.

**Key components**  
The figure is divided into two sections: Coarse-grained Counting and Fine-grained Counting. In each section, text alongside boxes within images explains the counting process. The coarse-grained example involves counting the number of men in a team photo, while the fine-grained example counts the bears on the ground, explicitly excluding any elevated ones.

**What the paper concludes from it**  
The authors conclude that using visual primitives, like bounding boxes, helps anchor entities during counting tasks. This method enhances the model's ability to handle coarse-grained and fine-grained counting by systematically scanning and verifying objects based on specific criteria, which reduces errors and enhances its robustness against hallucinations [page 8].

**Caveats / limits**  
The figure does not address how this approach performs in contexts with varied complexities beyond the examples shown, nor does it provide quantitative metrics of performance improvement. Additionally, while the figure aids visualization, assessing its actual impact on solving real-world counting tasks would require further empirical analysis.