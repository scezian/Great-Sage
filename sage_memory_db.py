import os
import logging

_log = logging.getLogger("great_sage.sage")

HAS_ML = True
try:
    import numpy as np
except ImportError:
    HAS_ML = False
    _log.warning("SageMemoryDB: numpy not installed — semantic memory disabled")


class SageMemoryDB:
    def __init__(self, db_path="~/.great_sage_memory_db.npz"):
        self.db_path = os.path.expanduser(db_path)
        self.model   = None  # Lazy-loaded on first use
        self.texts   = []
        self.embeddings = None
        self._load()

    def _init_model(self):
        global HAS_ML
        if self.model is None and HAS_ML:
            try:
                from sentence_transformers import SentenceTransformer
                self.model = SentenceTransformer("all-MiniLM-L6-v2")
                _log.debug("SageMemoryDB: sentence-transformer model loaded")
            except ImportError:
                HAS_ML = False
                _log.warning(
                    "SageMemoryDB: sentence_transformers not installed — "
                    "semantic memory disabled. Install with: "
                    "pip install sentence-transformers"
                )
            except Exception as e:
                HAS_ML = False
                _log.error("SageMemoryDB: model load failed", exc_info=e)

    def _load(self):
        if not os.path.exists(self.db_path):
            return
        try:
            data = np.load(self.db_path, allow_pickle=True)
            self.texts      = data["texts"].tolist()
            self.embeddings = data["embeddings"]
            _log.debug(
                "SageMemoryDB: loaded %d memories from %s",
                len(self.texts), self.db_path,
            )
        except Exception as e:
            # Corrupt or unreadable DB — back it up, start fresh
            _log.error(
                "SageMemoryDB: failed to load %s — starting with empty DB. "
                "Corrupt file backed up to %s.bak. Error: %s",
                self.db_path, self.db_path, e,
            )
            try:
                import shutil
                shutil.copy2(self.db_path, self.db_path + ".bak")
            except Exception:
                pass
            # Reset to a clean empty state — don't leave self in a half-initialised state
            self.texts      = []
            self.embeddings = None

    def _save(self):
        if self.embeddings is None or len(self.texts) == 0:
            return
        try:
            # Write to a temp file first so a crash during write can't corrupt the DB
            tmp_path = self.db_path + ".tmp"
            np.savez(
                tmp_path,
                texts=np.array(self.texts, dtype=object),
                embeddings=self.embeddings,
            )
            # Atomic rename — safe on Linux
            os.replace(tmp_path, self.db_path)
        except Exception as e:
            _log.error(
                "SageMemoryDB: failed to save to %s — memories may be lost. Error: %s",
                self.db_path, e,
            )

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
