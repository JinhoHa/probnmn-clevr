import copy
import os
import pathlib
from typing import Any, Dict, List, Optional, Union, Type

from loguru import logger
import torch
from torch import nn, optim


class CheckpointManager(object):
    r"""
    A :class:`CheckpointManager` periodically serializes models and other checkpointable objects
    (which implement ``state_dict`` method) as .pth files during training, and optionally keeps
    track of best performing checkpoint based on an observed metric.

    This class closely follows the API of PyTorch optimizers and learning rate schedulers.

    .. note::

        For :class:`~torch.nn.DataParallel` and :class:`~torch.nn.parallel.DistributedDataParallel`
        objects, ``module.state_dict`` is called instead of ``state_dict``.

    .. note::

        The observed metric for keeping best checkpoint is assumed "higher is better", flip the
        sign if otherwise.

    Parameters
    ----------
    serialization_dir: str
        Path to an empty or non-existent directory to save checkpoints.
    keep_recent: int, optional (default=10)
        Number of recent 'k' checkpoints to keep on disk. Older checkpoints will be removed.
        Set to a very large value for keeping all checkpoints.
    checkpointables: Any
        Keyword arguments with any checkpointable objects, for example: model, optimizer,
        learning rate scheduler. Their state dicts can be accessed as the name of keyword.

    Examples
    --------
    >>> model = torch.nn.Linear(10, 2)
    >>> optimizer = torch.optim.Adam(model.parameters())
    >>> ckpt_manager = CheckpointManager("/tmp/ckpt", model=model, optimizer=optimizer)
    >>> num_epochs = 20
    >>> for epoch in range(num_epochs):
    ...     train(model)
    ...     val_loss = validate(model)
    ...     ckpt_manager.step(- val_loss, epoch)
    """

    def __init__(
        self, serialization_dir: str = "/tmp", keep_recent: int = 10, **checkpointables: Any
    ):
        self.serialization_dir = pathlib.Path(serialization_dir)
        self.keep_recent = keep_recent

        # Shallow copy, keeps references to tensors as original objects.
        self.checkpointables = copy.copy(checkpointables)

        # Initialize members to hold state dict of best checkpoint and its performance.
        self._best_metric: float = -1e-12
        self._best_ckpt: Dict[str, Any] = {}

        # Keep epoch/iteration numbers of recently saved 'k' checkpoints.
        self._recent_iterations: List[int] = []

    def step(self, iteration: int, metric: Optional[float] = None):
        r"""Serialize checkpoint and update best checkpoint based on metric."""

        checkpointable_state_dict: Dict[str, Any] = self._state_dict()

        # We also checkpoint current iteration.
        checkpointable_state_dict["iteration"] = iteration

        # Update the best checkpoint based on metric, if provided.
        if metric is not None and metric > self._best_metric:
            self._best_metric = metric
            self._best_ckpt = copy.copy(checkpointable_state_dict)

        # Serialize checkpoint corresponding to current iteration.
        torch.save(
            checkpointable_state_dict, self.serialization_dir / f"checkpoint_{iteration}.pth"
        )
        # Serialize best performing checkpoint observed so far.
        torch.save(self._best_ckpt, self.serialization_dir / "checkpoint_best.pth")

        # Remove earliest checkpoint if there are more on disk.
        self._recent_iterations.append(iteration)
        if len(self._recent_iterations) > self.keep_recent:
            self.remove_earliest_checkpoint()

    def _state_dict(self):
        r"""Return a dict containing state dict of all checkpointables."""

        checkpointable_state_dict: Dict[str, Any] = {}
        for key in self.checkpointables:
            if isinstance(self.checkpointables[key], nn.DataParallel) or isinstance(
                self.checkpointables[key], nn.parallel.DistributedDataParallel
            ):
                checkpointable_state_dict[key] = self.checkpointables[key].module.state_dict()
            else:
                checkpointable_state_dict[key] = self.checkpointables[key].state_dict()

        return checkpointable_state_dict

    def remove_earliest_checkpoint(self):
        r"""Remove ealiest serialized checkpoint from disk."""

        earliest_iteration = self._recent_iterations.pop(0)
        (self._serialization_dir / f"checkpoint_{earliest_iteration}.pth").unlink()

    def load(self, checkpoint_path: str):
        r"""
        Load a serialized checkpoint from a path. This method will try to find each of
        :attr:`checkpointables` in the file and load its state dict. Since our checkpointables
        are held as references, this method does not return them.

        Parameters
        ----------
        checkpoint_path: str
            Path to a checkpoint serialized by :meth:`step`.

        Returns
        -------
        int
            Iteration corresponding to the loaded checkpoint. Useful for resuming training.
            This will be -1 in case of best checkpoint, or if info does not exist.
        """

        logger.info(f"Loading checkpoint from {checkpoint_path}")
        checkpoint = torch.load(checkpoint_path, map_location="cpu")
        iteration = checkpoint.pop("iteration", -1)

        # Keep flags of all checkpointables to lo which ones were not loaded.
        is_loaded = {key: False for key in self.checkpointables}

        # Load each checkpointable from checkpoint.
        for key in checkpoint:
            if key in self.checkpointables:
                logger.info(f"Loading {key} from {checkpoint_path}")

                # Handle case of DataParallel and DistributedDataParallel.
                if isinstance(self.checkpointables[key], nn.DataParallel) or isinstance(
                    self.checkpointables[key], nn.parallel.DistributedDataParallel
                ):
                    self.checkpointables[key].module.load_state_dict(checkpoint[key])
                else:
                    self.checkpointables[key].load_state_dict(checkpoint[key])

                is_loaded[key] = True
            else:
                logger.info(f"{key} not found in `checkpointables`.")

        not_loaded: List[str] = [key for key in is_loaded if not is_loaded[key]]
        logger.info(f"Checkpointables not found in file: {not_loaded}")
        return iteration
