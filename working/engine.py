import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn.modules.loss import _WeightedLoss
from torch.utils.data import Dataset, DataLoader
import torch.optim as optim

import pytorch_lightning as pl
from pytorch_lightning import seed_everything
from pytorch_lightning import loggers as pl_loggers
from pytorch_lightning import Callback
from pytorch_lightning.callbacks import ModelCheckpoint, LearningRateMonitor
from pytorch_lightning.callbacks.early_stopping import EarlyStopping

from .config import *
from .utils import *
from .loss import get_train_criterion, get_valid_criterion
from .models.models import nets, GeneralizedCassavaClassifier

class CassavaLitModule(pl.LightningModule):
    def __init__(self, net, fold):
        super(CassavaLitModule, self).__init__()

        self.net = net
        self.fold = fold

        self.train_criterion = get_train_criterion()
        self.valid_criterion = get_valid_criterion()
        self.train_losses = []
        self.valid_losses = []
        self.epoch = 0
        self.best_valid_loss = None
        self.current_epoch_train_loss = None

    def forward(self, x):
        return self.net(x)

#     def configure_optimizers(self):
#         optimizer = torch.optim.Adam(
#             params=self.parameters(), 
#             lr=LEARNING_RATE,
#             weight_decay=WEIGHT_DECAY
#         )
#         scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
#             optimizer,
#             patience=0,
#             factor=SCHEDULER_FACTOR,
#             verbose=LEARNING_VERBOSE
#         )
#         return {
#            'optimizer': optimizer,
#            'lr_scheduler': scheduler,
#            'monitor': 'val_loss'
#        }

    def configure_optimizers(self):
        optimizer = optim.AdamW(self.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=5, eta_min=0)

        return [optimizer], [scheduler]

    # def configure_optimizers(self):
    #     optimizer = torch.optim.Adam(
    #         params=self.parameters(), 
    #         lr=LEARNING_RATE,
    #         weight_decay=WEIGHT_DECAY
    #     )
    #     steps_per_epoch = len(self.train_dataloader())
    #     # steps_per_epoch = len(self.train_dataloader())//self.trainer.accumulate_grad_batches
    #     scheduler = torch.optim.lr_scheduler.OneCycleLR(
    #         optimizer=optimizer, 
    #         pct_start=0.1, 
    #         div_factor=1e3, 
    #         max_lr=1e-2,
    #         steps_per_epoch=steps_per_epoch, 
    #         epochs=MAX_EPOCHS
    #     )
    #     scheduler = {"scheduler": scheduler, "interval" : "step" }
    #     return [optimizer], [scheduler]

    def training_step(self, batch, batch_idx):        
        inputs, targets = batch
        outputs = self(inputs)

        loss = self.train_criterion(outputs, targets)

        self.log('train_loss', loss, on_step=True, on_epoch=True, prog_bar=False, logger=True)
        self.train_losses.append(loss.item())
        return loss

    def training_epoch_end(self, outputs):
        losses = 0.0
        for output in outputs:
            loss = output["loss"]
            losses += loss.item()

        losses /= len(outputs)

        self.current_epoch_train_loss = losses

    def validation_step(self, batch, batch_idx):
        inputs, targets = batch
        outputs = self(inputs)

        loss = self.valid_criterion(outputs, targets)

        self.log('val_loss', loss, on_step=True, on_epoch=True, prog_bar=False, logger=True)
        self.valid_losses.append(loss.item())
        return loss

    def validation_epoch_end(self, outputs):
        losses = 0.0
        for output in outputs:
            loss = output
            losses += loss.item()

        losses /= len(outputs)

        self.log('val_loss_epoch', losses, on_epoch=True, prog_bar=False, logger=True)

        if self.best_valid_loss is not None:
            self.best_valid_loss = min(self.best_valid_loss, losses)
        else:
            self.best_valid_loss = losses

        if self.current_epoch_train_loss is None:
            print(f"[{self.fold+1:>1}/{FOLDS:>1}][Epoch: {self.epoch:>2}] Training Loss: Sanity Check | Validation Loss: {losses:>.10f}")
        else:
            print(f"[{self.fold+1:>1}/{FOLDS:>1}][Epoch: {self.epoch:>2}] Training Loss: {self.current_epoch_train_loss:>.10f} | Validation Loss: {losses:>.10f}")
        self.epoch += 1

    def teardown(self, stage):
        train_fold_loss = sum(self.train_losses) / len(self.train_losses)
        valid_fold_loss = sum(self.valid_losses) / len(self.valid_losses)
        best_valid_fold_loss = self.best_valid_loss
        print(f"[{self.fold+1:>1}/{FOLDS:>1}] Training Loss: {train_fold_loss:>.10f} | Validation Loss: {valid_fold_loss:>.10f} | Best Validation Loss: {best_valid_fold_loss:>.10f}")

def get_net(name, fold, pretrained=False):
    if name not in nets.keys():
        net = GeneralizedCassavaClassifier(name, pretrained=pretrained)
    else:
        net = nets[name](pretrained=pretrained)

    return CassavaLitModule(net, fold)