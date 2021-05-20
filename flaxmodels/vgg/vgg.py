from jax import random
import jax.numpy as jnp
import flax.linen as nn
import functools
from typing import Any
import h5py
from .. import utils


URLS = {'vgg16': 'https://www.dropbox.com/s/ew3vhtlg5kks8mz/vgg16_weights.h5?dl=1',
        'vgg19': 'https://www.dropbox.com/s/1sn02fnkj579u1w/vgg19_weights.h5?dl=1'}


class VGG(nn.Module):
    output: str='softmax'
    pretrained: str='imagenet'
    architecture: str='vgg16'
    include_head: bool=True
    kernel_init: functools.partial=nn.initializers.lecun_normal()
    bias_init: functools.partial=nn.initializers.zeros
    ckpt_dir: str=None
    rng: Any=random.PRNGKey(0)

    def setup(self):
        if self.pretrained == 'imagenet':
            ckpt_file = utils.download(self.ckpt_dir, URLS[self.architecture])
            self.param_dict = h5py.File(ckpt_file, 'r')
        self.training = self.pretrained is None

    @nn.compact
    def __call__(self, x):
        """
        Args:
            x (tensor of shape [N, H, W, 3]):
                Batch of input images (RGB format). Images must be in range [0, 1].
                If 'include_head' is True, the images must be 224x224.
        """
        if self.output not in ['softmax', 'logits', 'activations']:
            raise ValueError('Wrong argument. Possible choices for output are "softmax", "logits", and "activations".')

        if self.pretrained is not None and self.pretrained != 'imagenet':
            raise ValueError('Wrong argument. Possible choices for pretrained are "imagenet" and None.')

        if self.include_head and (x.shape[1] != 224 or x.shape[2] != 224):
            raise ValueError('Wrong argument. If include_head is True, then input image must be of size 224x224.')

        if self.pretrained == 'imagenet':
            # normalize input
            mean = jnp.array([0.485, 0.456, 0.406]).reshape(1, 1, 1, -1)
            std = jnp.array([0.229, 0.224, 0.225]).reshape(1, 1, 1, -1)
            x = (x - mean) / std

        act = {}

        x = self._conv_block(x, features=64, num_layers=2, block_num=1, act=act)
        x = nn.max_pool(x, window_shape=(2, 2), strides=(2, 2))

        x = self._conv_block(x, features=128, num_layers=2, block_num=2, act=act)
        x = nn.max_pool(x, window_shape=(2, 2), strides=(2, 2))

        x = self._conv_block(x, features=256, num_layers=3 if self.architecture == 'vgg16' else 4, block_num=3, act=act)
        x = nn.max_pool(x, window_shape=(2, 2), strides=(2, 2))

        x = self._conv_block(x, features=512, num_layers=3 if self.architecture == 'vgg16' else 4, block_num=4, act=act)
        x = nn.max_pool(x, window_shape=(2, 2), strides=(2, 2))

        x = self._conv_block(x, features=512, num_layers=3 if self.architecture == 'vgg16' else 4, block_num=5, act=act)
        x = nn.max_pool(x, window_shape=(2, 2), strides=(2, 2))


        if self.include_head:
            # NCHW format because weights are from pytorch
            x = jnp.transpose(x, axes=(0, 3, 1, 2))
            x = jnp.reshape(x, (-1, x.shape[1] * x.shape[2] * x.shape[3]))
            x = self._fc_block(x, features=4096, block_num=6, relu=True, dropout=True, act=act)
            x = self._fc_block(x, features=4096, block_num=7, relu=True, dropout=True, act=act)
            x = self._fc_block(x, features=1000, block_num=8, relu=False, dropout=False, act=act)

        if self.output == 'activations':
            return act 

        if self.output == 'softmax' and self.include_head:
            x = nn.softmax(x)
        return x

    def _conv_block(self, x, features, num_layers, block_num, act):
        for l in range(num_layers):
            layer_name = f'conv{block_num}_{l + 1}'
            w = self.kernel_init if self.training else lambda *_ : jnp.array(self.param_dict[layer_name]['weight']) 
            b = self.bias_init if self.training else lambda *_ : jnp.array(self.param_dict[layer_name]['bias']) 
            x = nn.Conv(features=features, kernel_size=(3, 3), kernel_init=w, bias_init=b, padding='same', name=layer_name)(x)
            act[layer_name] = x
            x = nn.relu(x)
            act[f'relu{block_num}_{l + 1}'] = x
        return x

    def _fc_block(self, x, features, block_num, act, relu=False, dropout=False):
        layer_name = f'fc{block_num}'
        w = self.kernel_init if self.training else lambda *_ : jnp.array(self.param_dict[layer_name]['weight']) 
        b = self.bias_init if self.training else lambda *_ : jnp.array(self.param_dict[layer_name]['bias']) 
        x = nn.Dense(features=features, kernel_init=w, bias_init=b, name=layer_name)(x)
        act[layer_name] = x
        if relu:
            x = nn.relu(x)
            act[f'relu{block_num}'] = x
        if dropout: x = nn.Dropout(rate=0.5)(x, deterministic=not self.training, rng=self.rng)
        return x  


def VGG16(output='softmax',
          pretrained='imagenet',
          include_head=True,
          kernel_init=nn.initializers.lecun_normal(),
          bias_init=nn.initializers.zeros,
          ckpt_dir=None,
          rng=random.PRNGKey(0)):
    """
    Implementation of the VGG16 network by Simonyan & Zisserman.
    Reference: https://arxiv.org/abs/1409.1556.

    The pretrained parameters are taken from:
    https://github.com/pytorch/vision/blob/master/torchvision/models/vgg.py
    
    Args:
        output (str):
            Output of the module. Available options are:
                - 'softmax': Output is a softmax tensor of shape [N, 1000] 
                - 'logits': Output is a tensor of shape [N, 1000]
                - 'activations': Output is a dictionary containing the VGG activations
        pretrained (str):
            Indicates if and what type of weights to load. Options are:
                - 'imagenet': Loads the network parameters trained on ImageNet
                - None: Parameters of the module are initialized randomly
        include_head (bool):
            If True, include the three fully-connected layers at the top of the network.
            This option is useful when you want to obtain activations for images whose
            size is different than 224x224.
        kernel_init (function):
            A function that takes in a shape and returns a tensor.
        bias_init (function):
            A function that takes in a shape and returns a tensor.
        ckpt_dir (str):
            The directory to which the pretrained weights are downloaded.
            Only relevant if a pretrained model is used. 
            If this argument is None, the weights will be saved to a temp directory.
        rng (jax.numpy.ndarray): 
            Random seed.

    Returns:
        (nn.Module): VGG network.
    """
    return VGG(output=output,
               pretrained=pretrained,
               architecture='vgg16',
               include_head=include_head,
               kernel_init=kernel_init,
               bias_init=bias_init,
               ckpt_dir=ckpt_dir,
               rng=rng)


def VGG19(output='softmax',
          pretrained='imagenet',
          include_head=True,
          kernel_init=nn.initializers.lecun_normal(),
          bias_init=nn.initializers.zeros,
          ckpt_dir=None,
          rng=random.PRNGKey(0)):
    """
    Implementation of the VGG19 network by Simonyan & Zisserman.
    Reference: https://arxiv.org/abs/1409.1556.

    The pretrained parameters are taken from:
    https://github.com/pytorch/vision/blob/master/torchvision/models/vgg.py
    
    Args:
        output (str):
            Output of the module. Available options are:
                - 'softmax': Output is a softmax tensor of shape [N, 1000] 
                - 'logits': Output is a tensor of shape [N, 1000]
                - 'activations': Output is a dictionary containing the VGG activations
        pretrained (str):
            Indicates if and what type of weights to load. Options are:
                - 'imagenet': Loads the network parameters trained on ImageNet
                - None: Parameters of the module are initialized randomly
        include_head (bool):
            If True, include the three fully-connected layers at the top of the network.
            This option is useful when you want to obtain activations for images whose
            size is different than 224x224.
        kernel_init (function):
            A function that takes in a shape and returns a tensor.
        bias_init (function):
            A function that takes in a shape and returns a tensor.
        ckpt_dir (str):
            The directory to which the pretrained weights are downloaded.
            Only relevant if a pretrained model is used. 
            If this argument is None, the weights will be saved to a temp directory.
        rng (jax.numpy.ndarray): 
            Random seed.

    Returns:
        (nn.Module): VGG network.
    """
    return VGG(output=output,
               pretrained=pretrained,
               architecture='vgg19',
               include_head=include_head,
               kernel_init=kernel_init,
               bias_init=bias_init,
               ckpt_dir=ckpt_dir,
               rng=rng)

