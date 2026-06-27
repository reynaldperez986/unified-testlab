import argparse
import os
import sys


def validate_runtime() -> None:
    try:
        import torch
    except Exception as exc:
        raise RuntimeError(f"PyTorch is required for Unsloth training: {exc}") from exc

    if sys.platform == "win32":
        raise RuntimeError("Unsloth training is not supported in this workflow on native Windows. Use Linux or WSL2 with an NVIDIA CUDA GPU.")
    if not torch.cuda.is_available():
        raise RuntimeError("Unsloth requires an NVIDIA CUDA GPU. No CUDA device was detected.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a LoRA adapter with Unsloth and export GGUF.")
    parser.add_argument("--dataset", default=r"C:\web__automation\llm_workflow_assistant\finetune\fine_tune.jsonl")
    parser.add_argument("--base-model", default="llama3.2:3b")
    parser.add_argument("--ft-model-name", default="llama3-finetuned")
    parser.add_argument("--output-dir", default=os.path.join(os.path.dirname(__file__), "training_output"))
    args = parser.parse_args()

    validate_runtime()

    import unsloth
    from unsloth import FastLanguageModel
    from datasets import load_dataset
    from transformers import TrainingArguments
    from trl import SFTTrainer

    max_seq_length = 2048
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=args.base_model,
        max_seq_length=max_seq_length,
        dtype=None,
        load_in_4bit=True,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        lora_alpha=16,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=3407,
    )

    dataset = load_dataset("json", data_files=args.dataset, split="train")

    def format_row(row):
        instruction = str(row.get("instruction") or "").strip()
        input_text = str(row.get("input") or "").strip()
        output_text = str(row.get("output") or "").strip()
        prompt = f"### Instruction\n{instruction}\n\n"
        if input_text:
            prompt += f"### Input\n{input_text}\n\n"
        prompt += f"### Response\n{output_text}"
        return {"text": prompt}

    dataset = dataset.map(format_row)
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=max_seq_length,
        args=TrainingArguments(
            output_dir=args.output_dir,
            per_device_train_batch_size=2,
            gradient_accumulation_steps=4,
            warmup_steps=5,
            max_steps=60,
            learning_rate=2e-4,
            fp16=True,
            logging_steps=1,
            optim="adamw_8bit",
            weight_decay=0.01,
            lr_scheduler_type="linear",
            seed=3407,
        ),
    )
    trainer.train()

    os.makedirs(args.output_dir, exist_ok=True)
    model.save_pretrained_gguf(
        os.path.dirname(__file__),
        tokenizer,
        quantization_method="q4_k_m",
        gguf_file_name=f"{args.ft_model_name}.gguf",
    )
    print("GGUF exported to:", os.path.join(os.path.dirname(__file__), args.ft_model_name + ".gguf"))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] {exc}")
        raise SystemExit(1)
