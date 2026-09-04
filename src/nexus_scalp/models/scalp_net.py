"""
Institutional Causal Temporal Transformer PyTorch Scalping Neural Network (ScalpNet v3 - 50D Vector Aligned)
=============================================================================================================

Architectural Summary:
----------------------
ScalpNet is a dual-path deep learning architecture engineered for ultra-low latency,
high-frequency market microstructure classification. It processes continuous 50-dimensional
feature matrices (incorporating Order Flow Imbalance, ICT Fair Value Gaps, Smart Money Concepts,
Ichimoku Kumo projections, Wick Anatomy, and Cross-Asset Z-Scores).

Key Design Invariants:
----------------------
1. Zero Future Information Leakage:
   Temporal convolutions enforce strict left-side causal padding (1D Causal TCN), ensuring
   prediction at time-step `t` depends exclusively on history `<= t`.
2. Dual-Path Inference Routing:
   - 2D Single-Tick Mode: Routes through a high-speed ResNet MLP with residual LayerNorm
     for sub-millisecond hot-path inference.
   - 3D Sequence Mode: Routes through Dilated Causal Convolutions, Sinusoidal Positional Encoding,
     and Multi-Head Self-Attention for macro-pattern recognition.
3. Stable Gradient Dynamics:
   Pre-LayerNorm architectures and GeLU non-linearities protect against vanishing/exploding gradients
   on volatile commodities like Gold (XAUUSD).

Academic & Quantitative References:
-----------------------------------
- Deep, A., et al. (2025). "Deep Learning for High-Frequency Limit Order Book Dynamics."
- Briola, A., et al. (2024). "LOBFrame: Quantitative Foundation Framework for Machine Learning on Limit Order Books."
- Lucchese, M., et al. (2023). "Causal Convolutional Neural Networks and Transformers in Quantitative Finance."
- Vaswani, A., et al. (2017). "Attention Is All You Need." (NeurIPS).
- Lopez de Prado, M. (2018). "Advances in Financial Machine Learning." Wiley.
"""

import math
from typing import cast

import torch
import torch.nn.functional as F
from torch import nn


class CausalConv1d(nn.Module):
    """
    1D Causal Convolution layer that pads strictly on the left (past) temporal dimension.

    Guarantees strict causal invariance where future time-steps cannot leak into past activations.
    """

    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        kernel_size: int = 3,
        dilation: int = 1,
    ) -> None:
        super().__init__()
        self.padding = (kernel_size - 1) * dilation
        self.conv = nn.Conv1d(
            in_channels=in_channels,
            out_channels=out_channels,
            kernel_size=kernel_size,
            dilation=dilation,
            padding=0,  # Explicit left-side causal padding applied in forward pass
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Left-pad sequence in temporal dimension: (Left, Right) = (padding, 0).
        Input shape: (Batch, Hidden, Sequence_Length)
        """
        x_padded = F.pad(x, (self.padding, 0))
        return self.conv(x_padded)


class SinusoidalPositionalEncoding(nn.Module):
    """
    Injects deterministic Sinusoidal Positional Encoding to provide sequence position
    and time-distance awareness for the Multi-Head Self-Attention layers.
    """

    def __init__(self, hidden_dim: int, max_len: int = 500) -> None:
        super().__init__()
        pe = torch.zeros(max_len, hidden_dim)
        position = torch.arange(0, max_len, dtype=torch.float32).unsqueeze(1)
        div_term = torch.exp(
            torch.arange(0, hidden_dim, 2, dtype=torch.float32) * (-math.log(10000.0) / hidden_dim)
        )

        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        self.register_buffer("pe", pe.unsqueeze(0))  # Buffer Shape: (1, Max_Len, Hidden)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Adds positional encoding tensor to incoming latent sequence.
        Input shape: (Batch, Sequence_Length, Hidden)
        """
        seq_len = x.size(1)
        pe_tensor = cast(torch.Tensor, self.pe)
        return x + pe_tensor[:, :seq_len, :]


class ScalpNet(nn.Module):
    """
    Production Quantitative Scalping Network mapping multi-dimensional feature tensors
    to trade probability distributions.
    """

    def __init__(
        self,
        num_features: int = 50,  # 50D Master FeatureVector alignment
        num_classes: int = 4,  # Legacy serving default (MLFIX-T4): NO_TRADE/BUY/SELL + WAIT policy bridge
        hidden_dim: int = 128,  # Latent channel capacity
        num_heads: int = 4,  # Attention heads
        dropout_rate: float = 0.25,
    ) -> None:
        super().__init__()

        if not isinstance(num_classes, int) or num_classes < 2:
            raise ValueError(
                f"MODEL_CLASS_CONTRACT VIOLATION: ScalpNet num_classes must be an int >= 2, got {num_classes!r}"
            )
        self.num_features = num_features
        self.num_classes = num_classes
        self.hidden_dim = hidden_dim

        # 1. Feature Projection & Layer Normalization
        self.input_projection = nn.Linear(num_features, hidden_dim)
        self.input_norm = nn.LayerNorm(hidden_dim)

        # 2. ResNet MLP Path (Optimized for 2D single-tick snapshots)
        self.mlp_res1 = nn.Linear(hidden_dim, hidden_dim)
        self.mlp_res2 = nn.Linear(hidden_dim, hidden_dim)
        self.mlp_norm = nn.LayerNorm(hidden_dim)

        # 3. Causal Temporal Convolutional Network (1D Dilated Causal TCN)
        self.causal_conv1 = CausalConv1d(hidden_dim, hidden_dim, kernel_size=3, dilation=1)
        self.causal_conv2 = CausalConv1d(hidden_dim, hidden_dim, kernel_size=3, dilation=2)
        self.causal_conv3 = CausalConv1d(hidden_dim, hidden_dim, kernel_size=3, dilation=4)
        self.tcn_norm = nn.LayerNorm(hidden_dim)

        # 4. Positional Encoding & Multi-Head Self-Attention Block
        self.pos_encoder = SinusoidalPositionalEncoding(hidden_dim=hidden_dim)
        self.attention = nn.MultiheadAttention(
            embed_dim=hidden_dim,
            num_heads=num_heads,
            dropout=dropout_rate,
            batch_first=True,
        )
        self.attn_norm = nn.LayerNorm(hidden_dim)

        # 5. Deep Classification Head
        self.fc1 = nn.Linear(hidden_dim, hidden_dim // 2)
        self.fc2 = nn.Linear(hidden_dim // 2, hidden_dim // 4)
        self.dropout = nn.Dropout(dropout_rate)
        self.classifier = nn.Linear(hidden_dim // 4, num_classes)

    def forward(self, x: torch.Tensor, return_logits: bool = False) -> torch.Tensor:
        """
        Forward propagation supporting both 2D (Batch, Features) and 3D (Batch, Seq, Features) inputs.

        Args:
            x: Input tensor of shape (Batch, 50) or (Batch, Sequence_Length, 50).
            return_logits: If True, returns unnormalized logits (for Loss backpropagation).
                          If False, returns Softmax probability distribution (for Live Inference).

        Returns:
            torch.Tensor: Class probabilities or raw logits.
        """
        if self.num_classes < 2:
            raise RuntimeError(
                f"MODEL_CLASS_CONTRACT VIOLATION: num_classes={self.num_classes} < 2"
            )
        if self.classifier.out_features != self.num_classes:
            raise RuntimeError(
                f"MODEL_CLASS_CONTRACT VIOLATION: head classes {self.classifier.out_features} != declared {self.num_classes}"
            )
        is_2d_input = x.dim() == 2

        if is_2d_input:
            # Expand single 2D vector snapshot to sequence length 1: (Batch, 1, Features)
            x_seq = x.unsqueeze(1)
        else:
            x_seq = x

        # 1. Input Projection and Normalization
        h = self.input_norm(self.input_projection(x_seq))  # (Batch, Seq, Hidden)

        if is_2d_input:
            # Dual-Path A: 2D Snapshot ResNet MLP Path with Residual Skip Connection
            h_mlp = F.gelu(self.mlp_res1(h))
            h_mlp = self.dropout(h_mlp)
            h_mlp = F.gelu(self.mlp_res2(h_mlp))
            h = self.mlp_norm(h + h_mlp)
            h_pooled = h.squeeze(1)
        else:
            # Dual-Path B: 3D Sequence Causal TCN + Positional Attention Path
            h_tcn = h.transpose(1, 2)  # (Batch, Hidden, Seq)

            # Sequential Dilated Convolutions
            h_tcn = F.gelu(self.causal_conv1(h_tcn))
            h_tcn = F.gelu(self.causal_conv2(h_tcn))
            h_tcn = F.gelu(self.causal_conv3(h_tcn))

            h_tcn = h_tcn.transpose(1, 2)  # (Batch, Seq, Hidden)
            h = self.tcn_norm(h + h_tcn)  # Residual Skip Connection

            # Inject Positional Encoding & Apply Multi-Head Self-Attention
            h_pos = self.pos_encoder(h)
            attn_out, _ = self.attention(h_pos, h_pos, h_pos)
            h = self.attn_norm(h + attn_out)

            # Extract the most recent temporal state (t_last)
            h_pooled = h[:, -1, :]

        # 5. Deep Classification Head
        h_dense = F.gelu(self.fc1(h_pooled))
        h_dense = self.dropout(h_dense)
        h_dense = F.gelu(self.fc2(h_dense))

        logits = self.classifier(h_dense)

        # Loss functions need raw logits; live execution needs probabilities.
        # The 4-class order (NO_TRADE/BUY/SELL/WAIT) is fixed by the policy.
        if return_logits or self.training:
            return logits
        return F.softmax(logits, dim=-1)
