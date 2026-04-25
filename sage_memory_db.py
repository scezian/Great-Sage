import os

HAS_ML = True
try:
    import numpy as np
except ImportError:
    HAS_ML = False

class SageMemoryDB:
    def __init__(self, db_path="~/.great_sage_memory_db.npz"):
        self.db_path = os.path.expanduser(db_path)
        self.model = None  # Lazy load
        self.texts = []
        self.embeddings = None
        self._load()

    def _init_model(self):
        global HAS_ML
        if self.model is None and HAS_ML:
            try:
                from sentence_transformers import SentenceTransformer
                self.model = SentenceTransformer('all-MiniLM-L6-v2')
            except ImportError:
                HAS_ML = False

    def _load(self):
        if os.path.exists(self.db_path):
            try:
                data = np.load(self.db_path, allow_pickle=True)
                self.texts = data['texts'].tolist()
                self.embeddings = data['embeddings']
            except Exception as e:
                pass

    def _save(self):
        if self.embeddings is not None and len(self.texts) > 0:
            try:
                np.savez(self.db_path, texts=np.array(self.texts, dtype=object), embeddings=self.embeddings)
            except Exception as e:
                pass

    def add_memory(self, text: str):
        if not HAS_ML:
            return
            
        text = text.strip()
        if not text or text in self.texts:
            return
            
        self._init_model()
        vec = self.model.encode(text)
        self.texts.append(text)
        
        if self.embeddings is None:
            self.embeddings = np.array([vec])
        else:
            self.embeddings = np.vstack([self.embeddings, vec])
            
        self._save()

    def search(self, query: str, k: int = 3) -> list:
        if not HAS_ML or self.embeddings is None or len(self.texts) == 0:
            return []
            
        self._init_model()
        from sklearn.metrics.pairwise import cosine_similarity
        q_vec = self.model.encode(query)
        
        sims = cosine_similarity([q_vec], self.embeddings)[0]
        top_k_idx = np.argsort(sims)[-k:][::-1]
        
        results = [self.texts[i] for i in top_k_idx if sims[i] > 0.3]
        return results

    def dump_all(self):
        return self.texts.copy()
