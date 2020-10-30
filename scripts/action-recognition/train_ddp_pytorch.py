import os
import argparse

import torch
import torch.nn as nn
import torch.distributed as dist
import torch.optim
from tensorboardX import SummaryWriter

from gluoncv.torch.model_zoo import get_model
from gluoncv.torch.data import build_dataloader
from gluoncv.torch.utils.model_utils import deploy_model, load_model, save_model
from gluoncv.torch.utils.task_utils import train_classification, validation_classification
from gluoncv.torch.engine.config import get_cfg_defaults
from gluoncv.torch.engine.launch import spawn_workers
from gluoncv.torch.utils.utils import build_log_dir


def main_worker(cfg):
    # create tensorboard and logs
    if cfg.DDP_CONFIG.GPU_WORLD_RANK == 0:
        tb_logdir = build_log_dir(cfg)
        writer = SummaryWriter(log_dir=tb_logdir)
    else:
        writer = None
    cfg.freeze()

    # create model
    model = get_model(cfg)
    model = deploy_model(model, cfg)

    # create dataset and dataloader
    train_loader, val_loader, train_sampler, val_sampler, mg_sampler = build_dataloader(cfg)
    optimizer = torch.optim.SGD(model.parameters(), lr=cfg.CONFIG.TRAIN.LR, momentum=cfg.CONFIG.TRAIN.MOMENTUM,
                                weight_decay=cfg.CONFIG.TRAIN.W_DECAY)
    if cfg.CONFIG.MODEL.LOAD:
        model, _ = load_model(model, optimizer, cfg, load_fc=True)

    scheduler = torch.optim.lr_scheduler.MultiStepLR(optimizer, milestones=cfg.CONFIG.TRAIN.LR_MILESTONE, gamma=cfg.CONFIG.TRAIN.STEP)
    criterion = nn.CrossEntropyLoss().cuda()

    base_iter = 0
    for epoch in range(cfg.CONFIG.TRAIN.EPOCH_NUM):
        if cfg.DDP_CONFIG.DISTRIBUTED:
            train_sampler.set_epoch(epoch)

        base_iter = train_classification(base_iter, model, train_loader, epoch, criterion, optimizer, cfg, writer=writer)
        scheduler.step()
        if epoch % cfg.CONFIG.VAL.FREQ == 0 or epoch == cfg.CONFIG.TRAIN.EPOCH_NUM - 1:
            validation_classification(model, val_loader, epoch, criterion, cfg, writer)

        if epoch % cfg.CONFIG.LOG.SAVE_FREQ == 0:
            if cfg.DDP_CONFIG.GPU_WORLD_RANK == 0 or cfg.DDP_CONFIG.DISTRIBUTED == False:
                save_model(model, optimizer, epoch, cfg)
    if writer is not None:
        writer.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Train video action recognition models.')
    parser.add_argument('--config-file', type=str, help='path to config file.')
    args = parser.parse_args()

    cfg = get_cfg_defaults()
    cfg.merge_from_file(args.config_file)
    spawn_workers(main_worker, cfg)
