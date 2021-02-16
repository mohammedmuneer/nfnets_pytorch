import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np

"""
TODO
- VarianceScaling as weight init
"""

nfnet_params = {}

# F-series models
nfnet_params.update(**{
    'F0': {
        'width': [256, 512, 1536, 1536], 'depth': [1, 2, 6, 3],
        'train_imsize': 192, 'test_imsize': 256,
        'RA_level': '405', 'drop_rate': 0.2},
    'F1': {
        'width': [256, 512, 1536, 1536], 'depth': [2, 4, 12, 6],
        'train_imsize': 224, 'test_imsize': 320,
        'RA_level': '410', 'drop_rate': 0.3},
    'F2': {
        'width': [256, 512, 1536, 1536], 'depth': [3, 6, 18, 9],
        'train_imsize': 256, 'test_imsize': 352,
        'RA_level': '410', 'drop_rate': 0.4},
    'F3': {
        'width': [256, 512, 1536, 1536], 'depth': [4, 8, 24, 12],
        'train_imsize': 320, 'test_imsize': 416,
        'RA_level': '415', 'drop_rate': 0.4},
    'F4': {
        'width': [256, 512, 1536, 1536], 'depth': [5, 10, 30, 15],
        'train_imsize': 384, 'test_imsize': 512,
        'RA_level': '415', 'drop_rate': 0.5},
    'F5': {
        'width': [256, 512, 1536, 1536], 'depth': [6, 12, 36, 18],
        'train_imsize': 416, 'test_imsize': 544,
        'RA_level': '415', 'drop_rate': 0.5},
    'F6': {
        'width': [256, 512, 1536, 1536], 'depth': [7, 14, 42, 21],
        'train_imsize': 448, 'test_imsize': 576,
        'RA_level': '415', 'drop_rate': 0.5},
    'F7': {
        'width': [256, 512, 1536, 1536], 'depth': [8, 16, 48, 24],
        'train_imsize': 480, 'test_imsize': 608,
        'RA_level': '415', 'drop_rate': 0.5},
})

class NFNet(nn.Module):
    def __init__(self, num_classes, variant='F0'):
        super(NFNet, self).__init__()

        block_params = nfnet_params[variant]

        block_args = zip(
            block_params['width'],
            block_params['depth'],
            [0.5] * 4, # bottleneck pattern
            [128] * 4, # group pattern
            [True] * 4, # big width,
            [1, 2, 2, 2] # stride pattern
        )

        self.activation = nn.ReLU()
        self.drop_rate = block_params['drop_rate']
        self.num_classes = num_classes

        self.stem = nn.Sequential(
            WSConv2D(in_channels=3, out_channels=16, kernel_size=3, stride=2),
            self.activation,
            WSConv2D(in_channels=16, out_channels=32, kernel_size=3, stride=1),
            self.activation,
            WSConv2D(in_channels=32, out_channels=64, kernel_size=3, stride=1),
            self.activation,
            WSConv2D(in_channels=64, out_channels=128, kernel_size=3, stride=2)
        )

        expected_std = 1.0
        alpha = 0.2

        blocks = []
        in_channels = block_params['width'][0] // 2

        for (block_width, stage_depth, expand_ratio, group_size, big_width, stride) in block_args:
            for block_index in range(stage_depth):
                beta = 1. / expected_std

                out_channels = block_width
                # TODO: Stochastic Depth
                # Variables to implement: index, stochdepth_rate, num_blocks

                blocks.append(NFBlock(
                    in_channels=in_channels, 
                    out_channels=out_channels,
                    stride=stride if block_index == 0 else 1))

                in_channels = out_channels

                if block_index == 0:
                    expected_std = 1.0
                
                expected_std = (expected_std **2 + alpha**2)**0.5

        self.body = nn.Sequential(*blocks)

        final_conv_channels = 2*in_channels
        self.final_conv = WSConv2D(in_channels=out_channels, out_channels=final_conv_channels, kernel_size=1)
        self.pool = nn.AvgPool2d(1)
        
        if self.drop_rate > 0.:
            self.dropout = nn.Dropout(self.drop_rate)

        self.fc = nn.Linear(final_conv_channels, self.num_classes)

        nn.init.normal_(self.fc.weight, mean=0, std=0.01)


    def forward(self, x):
        out = self.stem(x)
        out = self.body(out)
        out = self.activation(self.final_conv(out))

        pool = torch.mean(out, dim=(2,3))

        if self.training:
            pool = self.dropout(pool)

        return self.fc(pool)

class NFBlock(nn.Module):
    def __init__(self, in_channels, out_channels, expansion=0.5, se_ratio=0.5, stride=1, beta=1.0, alpha=0.2, big_width=True):
        super(NFBlock, self).__init__()

        self.in_channels = in_channels
        self.out_channels = out_channels
        self.expansion = expansion
        self.se_ratio = se_ratio
        self.activation = nn.ReLU()
        self.beta, self.alpha = beta, alpha
        
        self.width = int((self.out_channels if big_width else self.in_channels) * expansion)
        self.stride = stride

        self.conv0 = WSConv2D(in_channels=self.in_channels, out_channels=self.width, kernel_size=1)
        self.conv1 = WSConv2D(in_channels=self.width, out_channels=self.width, kernel_size=3, stride=stride, padding=1)
        self.conv1b = WSConv2D(in_channels=self.width, out_channels=self.width, kernel_size=3, stride=1, padding=1)
        self.conv2 = WSConv2D(in_channels=self.width, out_channels=self.out_channels, kernel_size=1)
        
        self.use_projection = self.stride > 1 or self.in_channels != self.out_channels
        if self.use_projection:
            if stride > 1:
                self.shortcut_avg_pool = nn.AvgPool2d(kernel_size=2, stride=2, padding=0 if self.in_channels==1536 else 1)
            self.conv_shortcut = WSConv2D(self.in_channels, self.out_channels, kernel_size=1)
            
        self.se = SqueezeExcite(self.out_channels, self.out_channels, se_ratio=self.se_ratio)
        self.skip_gain = nn.Parameter(torch.zeros(1))

        # No stochastic depth implemented
        # No group size implemented
        


    def forward(self, x):
        out = self.activation(x) * self.beta

        if self.stride > 1:
            shortcut = self.shortcut_avg_pool(out)
            shortcut = self.conv_shortcut(shortcut)
        elif self.use_projection:
            shortcut = self.conv_shortcut(out)
        else:
            shortcut = x


        out = self.conv0(out)
        out = self.activation(out)
        out = self.conv1(out)
        out = self.activation(out)
        out = self.conv1b(out)
        out = self.activation(out)
        out = self.conv2(out)
        out = (self.se(out)*2) * out

        out = out * self.skip_gain
        return out * self.alpha + shortcut

class WSConv2D(nn.Conv2d):
    def __init__(self, in_channels: int, out_channels: int, kernel_size, stride = 1, padding = 0,
        dilation = 1, groups: int = 1, bias: bool = True, padding_mode: str = 'zeros'):

        super(WSConv2D, self).__init__(in_channels, out_channels, kernel_size, stride, 
            padding, dilation, groups, bias, padding_mode)
        
        nn.init.xavier_normal_(self.weight)
        self.gain = nn.Parameter(torch.ones(self.weight.shape[0]))
        self.eps = nn.Parameter(torch.tensor(1e-4))

    def standardized_weights(self, eps=1e-4):
        # Original code: HWCN
        weights = self.weight # NCHW
        mean = torch.mean(weights, dim=(1,2,3), keepdims=True)
        var = torch.var(weights, dim=(1,2,3), keepdims=True)
        fan_in = torch.prod(torch.Tensor(self.weight.shape[1:]))
        scale = torch.rsqrt(torch.maximum(var * fan_in, self.eps)) * self.gain.view_as(var)
        shift = mean * scale

        return weights * scale - shift
        
    def forward(self, x, eps=1e-4):
        weights = self.standardized_weights(eps)
        return super()._conv_forward(x, weights)

class SqueezeExcite(nn.Module):
    def __init__(self, in_channels, out_channels, se_ratio=0.5):
        super(SqueezeExcite, self).__init__()

        self.in_channels = in_channels
        self.out_channels = out_channels
        self.se_ratio = se_ratio

        self.hidden_channels = max(1, int(self.in_channels * self.se_ratio))
        
        self.activation = nn.ReLU()
        self.fc0 = nn.Linear(self.in_channels, self.hidden_channels)
        self.fc1 = nn.Linear(self.hidden_channels, self.out_channels)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        out = torch.mean(x, (2,3))
        out = self.fc1(self.activation(self.fc0(out)))
        out = self.sigmoid(out)

        b,c,_,_ = x.size()
        return out.view(b,c,1,1).expand_as(x)

if __name__=='__main__':
    device = torch.device('cpu')
    model = NFNet(80, 'F0')
    print(model)

    img = torch.randn(1, 3, 256, 256)

    model.eval()
    model.to(device)
    with torch.no_grad():
        res = model(img)

        print(res.shape)