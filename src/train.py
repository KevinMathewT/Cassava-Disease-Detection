import gc
import time
from joblib import Parallel, delayed

import torch
import torch.nn as nn

from .dataset import get_loaders
from .engine import get_device, get_net, get_optimizer_and_scheduler, train_one_epoch, valid_one_epoch
from . import config
from .utils import *
from .loss import get_train_criterion, get_valid_criterion

if config.USE_TPU:
    import torch_xla.core.xla_model as xm
    import torch_xla.distributed.xla_multiprocessing as xmp
    import torch_xla.distributed.parallel_loader as pl

import warnings
warnings.filterwarnings("ignore")


def run_fold(fold):
    create_dirs()
    print_fn = print if not config.USE_TPU else xm.master_print
    print_fn(f"___________________________________________________")
    print_fn(f"Training Model:              {config.NET}")
    print_fn(f"Training Fold:               {fold}")
    print_fn(f"Image Dimensions:            {config.H}x{config.W}")
    print_fn(f"Mixed Precision Training:    {config.MIXED_PRECISION_TRAIN}")
    print_fn(f"Training Batch Size:         {config.TRAIN_BATCH_SIZE}")
    print_fn(f"Validation Batch Size:       {config.VALID_BATCH_SIZE}")
    print_fn(f"Accumulate Iteration:        {config.ACCUMULATE_ITERATION}")

    global net
    train_loader, valid_loader          = get_loaders(fold)
    device                              = get_device(n=fold+1)
    net                                 = net.to(device)
    scaler                              = torch.cuda.amp.GradScaler() if not config.USE_TPU and config.MIXED_PRECISION_TRAIN else None
    loss_tr                             = get_train_criterion(device=device)
    loss_fn                             = get_valid_criterion(device=device)
    optimizer, scheduler                = get_optimizer_and_scheduler(net=net, dataloader=train_loader)

    gc.collect()

    for epoch in range(config.MAX_EPOCHS):
        epoch_start = time.time()

        train_mp_device_loader          = pl.ParallelLoader(train_loader, device).per_device_loader(device) if config.USE_TPU else train_loader
        train_one_epoch(fold, epoch, net, loss_tr, optimizer, train_mp_device_loader, device, scaler=scaler, scheduler=scheduler, schd_batch_update=config.SCHEDULER_BATCH_STEP)
        del train_mp_device_loader
        gc.collect()
        
        valid_mp_device_loader          = pl.ParallelLoader(valid_loader, device).per_device_loader(device) if config.USE_TPU else valid_loader
        valid_one_epoch(fold, epoch, net, loss_fn, valid_mp_device_loader, device, scheduler=None, schd_loss_update=False)
        del valid_mp_device_loader
        gc.collect()

        print_fn(f"Time Taken for Epoch {epoch}: {time.time() - epoch_start}")

        if config.USE_TPU:
            xm.save(net.state_dict(
            ), os.path.join(config.WEIGHTS_PATH, f'{config.NET}/{config.NET}_fold_{fold}_{epoch}.bin'))
        else:
            torch.save(net.state_dict(
            ), os.path.join(config.WEIGHTS_PATH, f'{config.NET}/{config.NET}_fold_{fold}_{epoch}.bin'))

    #torch.save(model.cnn_model.state_dict(),'{}/cnn_model_fold_{}_{}'.format(CFG['model_path'], fold, CFG['tag']))
    del net, optimizer, train_loader, valid_loader, scheduler
    torch.cuda.empty_cache()
    print_fn(f"___________________________________________________")


def tpu(rank, flags):
    global acc_list
    torch.set_default_tensor_type('torch.FloatTensor')
    res = run_fold(FLAGS['fold'])


def train():
    global net
    torch.cuda.empty_cache()
    if not config.USE_TPU:
        if not config.PARALLEL_FOLD_TRAIN:
            # for fold in range(2, FOLDS):
            #     run_fold(fold)
            # run_fold(0)
            for fold in [0]:
                net = get_net(name=config.NET, pretrained=config.PRETRAINED)
                run_fold(fold)

            # config.NET = "tf_efficientnet_b4_ns"

            # for fold in [0]:
            #     # global net
            #     net = get_net(name=config.NET, pretrained=config.PRETRAINED)
            #     run_fold(fold)

        if config.PARALLEL_FOLD_TRAIN:
            n_jobs = config.FOLDS
            parallel = Parallel(n_jobs=n_jobs, backend="threading")
            parallel(delayed(run_fold)(fold) for fold in range(config.FOLDS))

    if config.USE_TPU:
        # if config.MIXED_PRECISION_TRAIN:
        os.environ["XLA_USE_BF16"] = "1"
        os.environ["XLA_TENSOR_ALLOCATOR_MAXSIZE"] = "100000000"

        net = get_net(name=config.NET, pretrained=config.PRETRAINED)

        for fold in [0]:
            global FLAGS
            FLAGS = {"fold": fold}
            xmp.spawn(tpu, args=(FLAGS,), nprocs=8, start_method="fork")


if __name__ == "__main__":
    train()
