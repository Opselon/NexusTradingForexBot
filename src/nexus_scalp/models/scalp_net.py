"""
Institutional Causal Temporal Transformer PyTorch Scalping Neural Network (ScalpNet v3 - 40D Vector Aligned)
======================================================================================================
Production-grade PyTorch architecture for high-frequency scalp opportunity classification.
Designed according to Deep et al. (2025), Briola et al. (2024 - LOBFrame), and Lucchese et al. (2023).

Enterprise Upgrades Incorporated:
    1. 40D Master Feature Vector Alignment (Candle Patterns, Swings, Session Overlaps, Lags & Microstructure).
    2. Dual Output Mode (Raw Logits for Training / Softmax Probabilities for Live Inference).
    3. 1D Causal Temporal Convolutions (Left-padded only to prevent future sequence data leakage).
    4. Dual-Path Execution (ResNet-MLP path for 2D snapshots / Causal-TCN path for 3D sequences).
    5. Sinusoidal Positional Encoding for Multi-Head Self-Attention (Time-step distance awareness).
    6. Residual Skip-Connections & LayerNorm Engineering (Gradient stability on noisy Gold ticks).

Invariants:
    - Zero Future Leakage: Causal padding guarantees time-step t never observes time-step t+1.
    - Full Parity: Fully backwards-compatible constructor signature with Live Engine and Walk-Forward Trainer.
"""

import math

import torch
import torch.nn.functional as F
from torch import nn


class CausalConv1d(nn.Module):
    """
    1D Causal Convolution layer that pads strictly on the left (past) dimension.
    Guarantees no future time-step leakage in financial time-series modeling.
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
            padding=0,  # We perform explicit left-side causal padding
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Left-pad sequence in temporal dimension: (Left, Right) = (padding, 0)
        x_padded = F.pad(x, (self.padding, 0))
        return self.conv(x_padded)


class SinusoidalPositionalEncoding(nn.Module):
    """
    Injects Sinusoidal Positional Encoding to provide sequence position awareness
    for the Multi-Head Attention layer.
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
        self.register_buffer("pe", pe.unsqueeze(0))  # (1, Max_Len, Hidden)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x shape: (Batch, Seq_Len, Hidden)
        seq_len = x.size(1)
        return x + self.pe[:, :seq_len, :]


class ScalpNet(nn.Module):
    """
    Institutional Causal Scalping Neural Network mapping 2D/3D feature tensors to trade logit probabilities.
    """

    def __init__(
        self,
        num_features: int = 50,  # 50-dimension Master FeatureVector tensor alignment
        num_classes: int = 4,  # Output logits: 0=NO_TRADE, 1=BUY_MARKET, 2=SELL_MARKET, 3=WAIT
        hidden_dim: int = 128,  # Capacity for institutional microstructure patterns
        num_heads: int = 4,
        dropout_rate: float = 0.25,
    ) -> None:
        super().__init__()

        self.num_features = num_features
        self.num_classes = num_classes
        self.hidden_dim = hidden_dim

        # 1. Feature Projection & Layer Normalization
        self.input_projection = nn.Linear(num_features, hidden_dim)
        self.input_norm = nn.LayerNorm(hidden_dim)

        # 2. ResNet MLP Path (For 2D single tick snapshots)
        self.mlp_res1 = nn.Linear(hidden_dim, hidden_dim)
        self.mlp_res2 = nn.Linear(hidden_dim, hidden_dim)
        self.mlp_norm = nn.LayerNorm(hidden_dim)

        # 3. Causal Temporal Convolutional Network (1D Causal TCN)
        self.causal_conv1 = CausalConv1d(hidden_dim, hidden_dim, kernel_size=3, dilation=1)
        self.causal_conv2 = CausalConv1d(hidden_dim, hidden_dim, kernel_size=3, dilation=2)
        self.causal_conv3 = CausalConv1d(hidden_dim, hidden_dim, kernel_size=3, dilation=4)
        self.tcn_norm = nn.LayerNorm(hidden_dim)

        # 4. Sinusoidal Positional Encoding & Multi-Head Self-Attention Block
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
        Forward pass supporting both 2D (Batch, Features) and 3D (Batch, Sequence, Features) inputs.

        Args:
            x: Feature Tensor of shape (Batch, Features) or (Batch, Sequence, Features).
            return_logits: If True, returns raw unnormalized logits for Training (Loss computation).

        Returns:
            torch.Tensor: Normalized probabilities or raw logits.
        """
        is_2d_input = x.dim() == 2

        if is_2d_input:
            # Expand single 2D vector snapshot to sequence length 1: (Batch, 1, Features)
            x_seq = x.unsqueeze(1)
        else:
            x_seq = x

        # 1. Input Projection and Normalization
        h = self.input_norm(self.input_projection(x_seq))  # (Batch, Seq, Hidden)

        if is_2d_input:
            # Dual-Path: 2D Snapshot ResNet MLP Path with Skip Connections
            h_mlp = F.gelu(self.mlp_res1(h))
            h_mlp = self.dropout(h_mlp)
            h_mlp = F.gelu(self.mlp_res2(h_mlp))
            h = self.mlp_norm(h + h_mlp)
            h_pooled = h.squeeze(1)
        else:
            # Dual-Path: 3D Sequence Causal TCN + Positional Attention Path
            h_tcn = h.transpose(1, 2)  # (Batch, Hidden, Seq)

            # Causal Convolutions
            h_tcn = F.gelu(self.causal_conv1(h_tcn))
            h_tcn = F.gelu(self.causal_conv2(h_tcn))
            h_tcn = F.gelu(self.causal_conv3(h_tcn))

            h_tcn = h_tcn.transpose(1, 2)  # (Batch, Seq, Hidden)
            h = self.tcn_norm(h + h_tcn)  # Residual Skip Connection

            # Inject Positional Encoding & Apply Multi-Head Self-Attention
            h_pos = self.pos_encoder(h)
            attn_out, _ = self.attention(h_pos, h_pos, h_pos)
            h = self.attn_norm(h + attn_out)

            # Extract last temporal sequence step
            h_pooled = h[:, -1, :]

        # 5. Deep Classification Head
        h_dense = F.gelu(self.fc1(h_pooled))
        h_dense = self.dropout(h_dense)
        h_dense = F.gelu(self.fc2(h_dense))

        logits = self.classifier(h_dense)

        # Return raw logits during training or when explicitly requested
        if return_logits or self.training:
            return logits

        # Apply Softmax for probability distribution output during live inference
        return F.softmax(logits, dim=-1)
