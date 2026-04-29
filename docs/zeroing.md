# Dark Zero and Restore

On `LINEAR` heads, public readings always use the active zero offset.

- Factory zero is active by default
- `zero_dark()` replaces it with a user dark zero
- `restore_factory_zero()` switches back to the factory zero

## Dark Zero

```python
meter.zero_dark(frames=32, settle_s=0.2)
print(meter.zero_offsets_adc())
```

## Restore Factory Zero

```python
meter.restore_factory_zero()
print(meter.factory_zero_offsets_adc())
```

## Important Note

`LOG` frontends keep the existing behavior of the underlying instrument model and do not apply host-side zero subtraction.
