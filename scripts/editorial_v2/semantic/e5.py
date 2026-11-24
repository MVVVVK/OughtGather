"""CPU E5 encoder; loads an explicitly prefetched pinned model, never downloads."""
MODEL = 'intfloat/multilingual-e5-small'
REVISION = 'fd1525a9fd15316a2d503bf26ab031a61d056e98'
MAX_TOKENS = 512


class E5Encoder:
    def __init__(self, batch_size=16):
        if type(batch_size) is not int or not 1 <= batch_size <= 128:
            raise ValueError('invalid batch size')
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
            self.torch = torch
            self.tokenizer = AutoTokenizer.from_pretrained(MODEL, revision=REVISION, local_files_only=True)
            self.model = AutoModel.from_pretrained(MODEL, revision=REVISION, local_files_only=True, use_safetensors=True).to('cpu').eval()
        except (ImportError, OSError) as exc:
            raise RuntimeError('pinned E5 dependencies or cached model unavailable') from exc
        self.batch_size = batch_size

    def encode(self, texts):
        output = []
        for start in range(0, len(texts), self.batch_size):
            inputs = ['query: ' + text for text in texts[start:start + self.batch_size]]
            batch = self.tokenizer(inputs, padding=True, truncation=True, max_length=MAX_TOKENS, return_tensors='pt')
            with self.torch.inference_mode():
                hidden = self.model(**batch).last_hidden_state
                mask = batch['attention_mask'].unsqueeze(-1)
                pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
                vectors = self.torch.nn.functional.normalize(pooled, p=2, dim=1)
            output.extend(vectors.cpu().tolist())
        return output
