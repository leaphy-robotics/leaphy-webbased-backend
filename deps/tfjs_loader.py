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
    try:
        model = keras.models.model_from_json(json.dumps(model_topology))
    except Exception:
        # Keras 3 might fail to load old TFJS/Keras 2 formats directly.
        # Try manual reconstruction as a fallback.
        if model_topology.get("class_name") == "Sequential":
            layers_config = model_topology["config"]["layers"]
            model = keras.Sequential()
            for layer_cfg in layers_config:
                class_name = layer_cfg["class_name"]
                config = layer_cfg["config"]

                # Remove Keras 3 incompatible args or args that should be handled differently
                if "batch_input_shape" in config:
                    input_shape = config.pop("batch_input_shape")
                    if len(model.layers) == 0:
                        model.add(keras.layers.InputLayer(shape=input_shape[1:]))

                # Some initializers might need fixing if they are not standard
                # For now, we assume standard layers are available in keras.layers
                layer_cls = getattr(keras.layers, class_name)
                # Remove name to avoid potential conflicts
                config.pop("name", None)
                model.add(layer_cls(**config))
        else:
            raise

    # Load weights from the binary file
    with open(weights_bin_path, "rb") as f:
        weights_data = f.read()

    weight_entries = decode_weights(config_json["weightsManifest"], [weights_data])

    weights_dict = {entry["name"]: entry["data"] for entry in weight_entries}

    # Map weights to model layers
    weights_list = []
    for layer in model.layers:
        if isinstance(layer, keras.layers.InputLayer):
            continue
        for weight in layer.weights:
            # TFJS names usually don't have :0, Keras names do.
            # We need to normalize both to match.
            normalized_name = normalize_weight_name(weight.name)
            simple_name = normalized_name.split("/")[-1]

            # Try matching by full normalized name, then by simple name + shape
            found = False
            if normalized_name in weights_dict and weights_dict[normalized_name].shape == weight.shape:
                weights_list.append(weights_dict[normalized_name])
                found = True
            else:
                for name, data in weights_dict.items():
                    if (name.endswith(simple_name) or simple_name.endswith(name)) and data.shape == weight.shape:
                        weights_list.append(data)
                        found = True
                        break

            if not found:
                raise ValueError(
                    f"Weight {weight.name} (normalized: {normalized_name}) not found in weights manifest with shape {weight.shape}"
                )

    model.set_weights(weights_list)
    return model
