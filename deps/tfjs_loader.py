"""Module for loading TensorFlow.js models into Keras."""

import json
from typing import Any, Dict, List

import numpy as np
import keras


def normalize_weight_name(weight_name: str) -> str:
    """Remove suffix ":0" (if present) from weight name."""
    if weight_name.endswith(":0"):
        return weight_name[:-2]
    return weight_name


def decode_weights(
    weights_manifest: List[Dict[str, Any]], data_buffers: List[bytes]
) -> List[Dict[str, Any]]:
    """Simplified version of tensorflowjs.read_weights.decode_weights"""
    out_group: List[Dict[str, Any]] = []
    for group, data_buffer in zip(weights_manifest, data_buffers):
        offset = 0
        for weight in group["weights"]:
            name = weight["name"]
            dtype = np.dtype(weight["dtype"])
            shape = weight["shape"]

            weight_numel = 1
            for dim in shape:
                weight_numel *= dim

            value = np.frombuffer(
                data_buffer, dtype=dtype, count=weight_numel, offset=offset
            ).reshape(shape)
            offset += dtype.itemsize * value.size
            out_group.append({"name": name, "data": value})
    return out_group


def load_tfjs_model(model_json_path: str, weights_bin_path: str) -> keras.Model:
    """Load a TensorFlow.js model from JSON and binary weights files."""
    with open(model_json_path, "r", encoding="utf-8") as f:
        config_json = json.load(f)

    model_topology = config_json["modelTopology"]
    if "model_config" in model_topology:
        model_topology = model_topology["model_config"]

    # Reconstruct the Keras model from the topology JSON
    model = keras.models.model_from_json(json.dumps(model_topology))

    # Load weights from the binary file
    with open(weights_bin_path, "rb") as f:
        weights_data = f.read()

    weight_entries = decode_weights(config_json["weightsManifest"], [weights_data])

    weights_dict = {entry["name"]: entry["data"] for entry in weight_entries}

    # Map weights to model layers
    weights_list = []
    for layer in model.layers:
        for weight in layer.weights:
            # TFJS names usually don't have :0, Keras names do.
            # We need to normalize both to match.
            normalized_name = normalize_weight_name(weight.name)
            if normalized_name in weights_dict:
                weights_list.append(weights_dict[normalized_name])
            else:
                # Some versions of TF/Keras might have different naming conventions
                # for nested layers. This is a simple fallback.
                found = False
                for name in weights_dict:
                    if normalized_name.endswith(name):
                        weights_list.append(weights_dict[name])
                        found = True
                        break
                if not found:
                    raise ValueError(
                        f"Weight {weight.name} (normalized: {normalized_name}) not found in weights manifest"
                    )

    model.set_weights(weights_list)
    return model
