import argparse
import time
import os
import os.path as osp
import shutil

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim

from deepul_helper.data import get_datasets
from deepul_helper.utils import AverageMeter, ProgressMeter, remove_module_state_dict
from deepul_helper.tasks import *


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-d', '--dataset', type=str, default='imagenet')
    parser.add_argument('-t', '--task', type=str, required=True,
                        help='context_encoder|rotation|cpc|simclr')
    parser.add_argument('--lr', type=float, default=0.1)
    parser.add_argument('-e', '--epochs', type=int, default=50)
    parser.add_argument('-b', '--batch_size', type=int, default=128)
    parser.add_argument('-i', '--log_interval', type=int, default=10)
    args = parser.parse_args()

    model_dir = osp.join('results', f'{args.dataset}_{args.task}')
    args.output_dir = osp.join(model_dir, 'linear_classifier')
    assert osp.exists(model_dir)
    if not osp.exists(args.output_dir):
        os.makedirs(args.output_dir)

    train_dset, test_dset, n_classes = get_datasets(args.dataset, args.task)
    train_loader = torch.utils.data.DataLoader(
        train_dset, batch_size=args.batch_size, num_workers=4,
        pin_memory=True
    )
    test_loader = torch.utils.data.DataLoader(
        test_dset, batch_size=args.batch_size, num_workers=4,
        pin_memory=True
    )

    if args.task == 'context_encoder':
        model = ContextEncoder(args.dataset, n_classes)
    elif args.task == 'rotation':
        model = RotationPrediction(args.dataset, n_classes)
    elif args.task == 'cpc':
        model = CPC(args.dataset, n_classes)
    elif args.task == 'simclr':
        model = SimCLR(args.dataset, n_classes)
    else:
        raise Exception('Invalid task:', args.task)
    model = model.cuda()
    model_path = osp.join(model_dir, 'model_best.pth.tar')
    checkpoint = torch.load(model_path, map_location='cuda')
    state_dict = remove_module_state_dict(checkpoint['state_dict'])
    model.load_state_dict(state_dict)
    model.eval()

    linear_classifier = model.construct_classifier().cuda()
    optimizer = optim.SGD(linear_classifier.parameters(), lr=args.lr, momentum=0.9, weight_decay=5e-4)

    best_acc = 0
    for epoch in range(args.epochs):
        train(train_loader, model, linear_classifier, optimizer, epoch, args)
        acc = validate(test_loader, model, linear_classifier, args)

        is_best = acc > best_acc
        best_acc = max(acc, best_acc)
        save_checkpoint({
            'epoch': epoch + 1,
            'state_dict': linear_classifier.state_dict(),
            'best_acc': best_acc,
            'optimizer': optimizer.state_dict()
        }, is_best, args)


def train(train_loader, model, linear_classifier, optimizer, epoch, args):
    batch_time = AverageMeter('Time', ':6.3f')
    data_time = AverageMeter('Data', ':6.3f')
    losses = AverageMeter('Loss', ':.4e')
    top1 = AverageMeter('Acc@1', ':6.2f')
    top5 = AverageMeter('Acc@5', ':6.2f')
    progress = ProgressMeter(
        len(train_loader),
        [batch_time, data_time, losses, top1, top5],
        prefix="Epoch: [{}]".format(epoch)
    )

    linear_classifier.train()

    end = time.time()
    for i, (images, target) in enumerate(train_loader):
        data_time.update(time.time() - end)

        images = images.cuda(non_blocking=True)
        target = target.cuda(non_blocking=True)

        with torch.no_grad():
            zs = model.encode(images)
        logits = linear_classifier(zs)
        loss = F.cross_entropy(logits, target)

        acc1, acc5 = accuracy(logits, target, topk=(1, 5))
        losses.update(loss.item(), images.size(0))
        top1.update(acc1[0], images.size(0))
        top5.update(acc5[0], images.size(0))

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        batch_time.update(time.time() - end)
        end = time.time()

        if i % args.log_interval == 0:
            progress.display(i)


def validate(test_loader, model, linear_classifier, args):
    batch_time = AverageMeter('Time', ':6.3f')
    losses = AverageMeter('Loss', ':.4e')
    top1 = AverageMeter('Acc@1', ':6.2f')
    top5 = AverageMeter('Acc@5', ':6.2f')
    progress = ProgressMeter(
        len(test_loader),
        [batch_time, losses, top1, top5],
        prefix='Test: '
    )

    linear_classifier.eval()

    with torch.no_grad():
        end = time.time()
        for i, (images, target) in enumerate(test_loader):
            images = images.cuda(non_blocking=True)
            target = target.cuda(non_blocking=True)

            zs = model.encode(images)
            logits = linear_classifier(zs)
            loss = F.cross_entropy(logits, target)

            acc1, acc5 = accuracy(logits, target, topk=(1, 5))
            losses.update(loss.item(), images.size(0))
            top1.update(acc1[0], images.size(0))
            top5.update(acc5[0], images.size(0))

            batch_time.update(time.time() - end)
            end = time.time()

            if i % args.log_interval == 0:
                progress.display(i)

    print(' * Acc@1 {top1.avg:.3f} Acc@5 {top5.avg:.3f}'
          .format(top1=top1, top5=top5))

    return top1.avg


def save_checkpoint(state, is_best, args, filename='checkpoint.pth.tar'):
    filename = osp.join(args.output_dir, filename)
    torch.save(state, filename)
    if is_best:
        shutil.copyfile(filename, osp.join(args.output_dir, 'model_best.pth.tar'))


def accuracy(output, target, topk=(1,)):
    with torch.no_grad():
        maxk = max(topk)
        batch_size = target.size(0)

        _, pred = output.topk(maxk, 1, True, True)
        pred = pred.t()
        correct = pred.eq(target.view(1, -1).expand_as(pred))

        res = []
        for k in topk:
            correct_k = correct[:k].view(-1).float().sum(0, keepdim=True)
            res.append(correct_k.mul_(100.0 / batch_size))
        return res


if __name__ == '__main__':
    main()
