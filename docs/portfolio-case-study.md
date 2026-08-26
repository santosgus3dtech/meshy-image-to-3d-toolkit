# Portfolio Case Study: Image-to-3D Automation

## Problem

Manual image-to-3D experiments create a lot of fragile steps: uploading images, waiting for long-running jobs, downloading models, checking printability, separating colors, and repairing geometry.

## Solution

This toolkit wraps the Meshy API in repeatable CLIs and adds local post-processing helpers for a practical 3D printing workflow.

## Highlights

- API client with polling, terminal status handling, and timeout control.
- Artifact capture for model files, thumbnails, and task JSON.
- Multi-image input flow for front/side/back references.
- Print-specific helpers for Meshy print endpoints.
- Color splitting and Blender voxel repair scripts for slicer preparation.

## Why it matters

The code shows the difference between a demo script and a useful production workflow: predictable outputs, clear command-line flags, ignored generated assets, and failure messages that help the operator recover.
