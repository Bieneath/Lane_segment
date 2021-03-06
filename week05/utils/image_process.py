import os

from numpy.core.defchararray import encode
import cv2
import random
import torch
import numpy as np
from torch.utils.data import Dataset
from imgaug import augmenters as iaa
# 关于imgaug请参考：https://blog.csdn.net/u012897374/article/details/80142744
from utils.process_labels import encode_labels, decode_labels, decode_color_labels
import lmdb

#这里定义sometimes意味:有时候做
#实际上在深度学习的模型训练中，数据增强不能喧宾夺主
# 如果对每一张图片都加入高斯模糊的话实际上是毁坏了原来数据的特征
# 因此，我们需要“有时候”做，给这个操作加一个概率。
# 参考：https://blog.csdn.net/qq_36552489/article/details/107645788
sometimes = lambda aug: iaa.Sometimes(0.5, aug)


# crop the image to discard useless parts
def crop_resize_data(image, label=None, image_size=(1024, 384), offset=690):
    """
    Attention:
    h,w,c = image.shape
    cv2.resize(image,(w,h))
    """
    roi_image = image[offset:, :]
    if label is not None:
        roi_label = label[offset:, :]
        train_image = cv2.resize(roi_image, image_size, interpolation=cv2.INTER_LINEAR)
        train_label = cv2.resize(roi_label, image_size, interpolation=cv2.INTER_NEAREST)
        return train_image, train_label
    else:
        train_image = cv2.resize(roi_image, image_size, interpolation=cv2.INTER_LINEAR)
        return train_image

# 读取LMDB数据方法，请结合make_lmdb使用！
class LaneDataset(Dataset):
    def __init__(self, lmdb_file, transform=None):
        super(LaneDataset, self).__init__()
        
        self.env = lmdb.open(lmdb_file)
        self.txn = self.env.begin()
        self.n_samples = self.txn.stat()['entries'] // 2 # 样本对数为txn中总数的1/2

        self.transform = transform

    def __len__(self):
        return self.n_samples
    
    def __getitem__(self, idx):
        image_name = 'image_%03d'%(idx)
        label_name = 'label_%03d'%(idx)
        ori_image_bn = self.txn.get(image_name.encode())
        ori_mask_bn = self.txn.get(label_name.encode())

        ori_image = np.frombuffer(ori_image_bn, dtype=np.uint8)
        ori_image = cv2.imdecode(ori_mask, cv2.IMREAD_COLOR) # 这步是必须的，将一维数据处理成二维图像
        ori_mask = np.frombuffer(ori_mask_bn, dtype=np.uint8)
        ori_mask = cv2.imdecode(ori_mask, cv2.IMREAD_GRAYSCALE) # 这行对应源码中mask读取灰度图

        # # 释放缓存
        # self.txn.close()
        # self.env.close()

        # 下面照抄原来的LaneDataset函数
        train_img, train_mask = crop_resize_data(ori_image, ori_mask)
        # Encode
        train_mask = encode_labels(train_mask)
        sample = [train_img.copy(), train_mask.copy()]
        if self.transform:
            sample = self.transform(sample)
        return sample

# 以下为直接读取目录下的图片数据方法
class LaneDataset_directly(Dataset):

    def __init__(self, csv_file, transform=None):
        super(LaneDataset_directly, self).__init__()
        
        with open(csv_file, 'r') as f:
            lines = f.readlines()
            self.paths = [line.strip().split(', ') for line in lines]
        self.images = [path[0] for path in self.paths]
        self.labels = [path[1] for path in self.paths]

        self.transform = transform

    def __len__(self):
        return len(self.images)

    def __getitem__(self, idx):

        ori_image = cv2.imread(self.images[idx])
        ori_mask = cv2.imread(self.labels[idx], cv2.IMREAD_GRAYSCALE)
        train_img, train_mask = crop_resize_data(ori_image, ori_mask)
        # Encode
        train_mask = encode_labels(train_mask)
        sample = [train_img.copy(), train_mask.copy()]
        if self.transform:
            sample = self.transform(sample)
        return sample


# pixel augmentation
class ImageAug(object):
    def __call__(self, sample):
        image, mask = sample
        if np.random.uniform(0,1) > 0.5:
            seq = iaa.Sequential([iaa.OneOf([
                iaa.AdditiveGaussianNoise(scale=(0, 0.2 * 255)),
                iaa.Sharpen(alpha=(0.1, 0.3), lightness=(0.7, 1.3)),
                iaa.GaussianBlur(sigma=(0, 1.0))])])
            image = seq.augment_image(image)
        return image, mask


# deformation augmentation
class DeformAug(object):
    def __call__(self, sample):
        image, mask = sample
        seq = iaa.Sequential([iaa.CropAndPad(percent=(-0.05, 0.1))])
        seg_to = seq.to_deterministic()
        image = seg_to.augment_image(image)
        mask = seg_to.augment_image(mask)
        return image, mask


class ScaleAug(object):
    def __call__(self, sample):
        image, mask = sample
        scale = random.uniform(0.7, 1.5)
        h, w, _ = image.shape
        aug_image = image.copy()
        aug_mask = mask.copy()
        aug_image = cv2.resize(aug_image, (int (scale * w), int (scale * h)))
        aug_mask = cv2.resize(aug_mask, (int (scale * w), int (scale * h)))
        if (scale < 1.0):
            new_h, new_w, _ = aug_image.shape
            pre_h_pad = int((h - new_h) / 2)
            pre_w_pad = int((w - new_w) / 2)
            pad_list = [[pre_h_pad, h - new_h - pre_h_pad], [pre_w_pad, w - new_w - pre_w_pad], [0, 0]]
            aug_image = np.pad(aug_image, pad_list, mode="constant")
            aug_mask = np.pad(aug_mask, pad_list[:2], mode="constant")
        if (scale > 1.0):
            new_h, new_w, _ = aug_image.shape
            pre_h_crop = int ((new_h - h) / 2)
            pre_w_crop = int ((new_w - w) / 2)
            post_h_crop = h + pre_h_crop
            post_w_crop = w + pre_w_crop
            aug_image = aug_image[pre_h_crop:post_h_crop, pre_w_crop:post_w_crop]
            aug_mask = aug_mask[pre_h_crop:post_h_crop, pre_w_crop:post_w_crop]
        return aug_image, aug_mask


class CutOut(object):
    def __init__(self, mask_size, p):
        self.mask_size = mask_size
        self.p = p

    def __call__(self, sample):
        image, mask = sample
        mask_size_half = self.mask_size // 2
        offset = 1 if self.mask_size % 2 == 0 else 0

        h, w = image.shape[:2]
        cxmin, cxmax = mask_size_half, w + offset - mask_size_half
        cymin, cymax = mask_size_half, h + offset - mask_size_half
        cx = np.random.randint(cxmin, cxmax)
        cy = np.random.randint(cymin, cymax)
        xmin, ymin = cx - mask_size_half, cy - mask_size_half
        xmax, ymax = xmin + self.mask_size, ymin + self.mask_size
        xmin, ymin, xmax, ymax = max(0, xmin), max(0, ymin), min(w, xmax), min(h, ymax)
        if np.random.uniform(0, 1) < self.p:
            image[ymin:ymax, xmin:xmax] = (0, 0, 0)
        return image, mask


class ToTensor(object):
    def __call__(self, sample):

        image, mask = sample
        image = np.transpose(image,(2,0,1))
        image = image.astype(np.float32)
        mask = mask.astype(np.long)
        return {'image': torch.from_numpy(image.copy()),
                'mask': torch.from_numpy(mask.copy())}


def expand_resize_data(prediction=None, submission_size=(3384, 1710), offset=690):
    pred_mask = decode_labels(prediction)
    expand_mask = cv2.resize(pred_mask, (submission_size[0], submission_size[1] - offset), interpolation=cv2.INTER_NEAREST)
    submission_mask = np.zeros((submission_size[1], submission_size[0]), dtype='uint8')
    submission_mask[offset:, :] = expand_mask
    return submission_mask


def expand_resize_color_data(prediction=None, submission_size=(3384, 1710), offset=690):
    color_pred_mask = decode_color_labels(prediction)
    color_pred_mask = np.transpose(color_pred_mask, (1, 2, 0))
    color_expand_mask = cv2.resize(color_pred_mask, (submission_size[0], submission_size[1] - offset), interpolation=cv2.INTER_NEAREST)
    color_submission_mask = np.zeros((submission_size[1], submission_size[0], 3), dtype='uint8')
    color_submission_mask[offset:, :, :] = color_expand_mask
    return color_sub_mission_mask
