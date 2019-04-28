from typing import Any, Dict, List, Optional, Type

import torch
from torch import nn
from torch.utils.data import DataLoader
from tqdm import tqdm

from probnmn.config import Config


class _Evaluator(object):
    """A base class for generic evaluator which can have multiple models interacting with each
    other. An implementation of a class extending this evaluator will contain the core evaluation
    loop logic. This base class offers full flexibility, with sensible defaults which may be
    changed or disabled while extending this class.
    """

    def __init__(
        self,
        config: Config,
        dataloader: DataLoader,
        models: Dict[str, Type[nn.Module]],
        gpu_ids: List[int] = [0],
    ):
        self._C = config
        self._dataloader = dataloader
        self._models = models

        # Set device according to specified GPU ids. This device is only required for batches,
        # models will already be on apropriate device already, if passed from trainer.
        self._device = torch.device(f"cuda:{gpu_ids[0]}" if gpu_ids[0] >= 0 else "cpu")

    @property
    def models(self):
        return self._models

    def evaluate(self, num_batches: Optional[int] = None):

        # Switch all models to "eval" mode.
        for model_name in self._models:
            self._models[model_name].eval()

        with torch.no_grad():
            for iteration, batch in enumerate(tqdm(self._dataloader, desc="validation")):
                for key in batch:
                    batch[key] = batch[key].to(self._device)

                _ = self._do_iteration(batch)
                if num_batches is not None and iteration > num_batches:
                    break

        # keys: `self._models.keys()`
        eval_metrics: Dict[str, Dict[str, Any]] = {}
        for model_name in self._models:

            # Get metrics recorded by a particular model. This `hasattr` check exists because
            # it is a generic base class, all the models in `probnmn.models` implement a
            # `get_metrics` method.
            if hasattr(self._models[model_name], "get_metrics"):
                # keys: names of metrics recorded by corresponding model.
                eval_metrics[model_name] = self._models[model_name].get_metrics()
            elif isinstance(self._models[model_name], nn.DataParallel):
                if hasattr(self._models[model_name].module, "get_metrics"):
                    eval_metrics[model_name] = self._models[model_name].module.get_metrics()

        # Switch all models back to "train" mode.
        for model_name in self._models:
            self._models[model_name].train()

        return eval_metrics

    def _do_iteration(self, batch: Dict[str, Any]) -> Dict[str, Any]:
        # Dummy implementation - just do a forward pass for some "model".
        output_dict = self._models["model"](batch)
        return output_dict