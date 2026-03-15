# Claude Code Skill for E2SAR Integration

This project has an associated Claude Code skill that provides reference patterns and implementation guidance for EJFAT/E2SAR integration.

## Skill Information

**Skill Name**: `e2sar-integration`
**Version**: 1.0.0
**Location**: `~/.claude/skills/e2sar-integration/`

## What the Skill Provides

The skill contains complete reference implementations and patterns for:

1. **E2SAR Segmenter (Data Handler)** - Sending data via EJFAT
2. **E2SAR Reassembler (Event Source)** - Receiving data via EJFAT
3. **Pydantic Configuration Models** - Type-safe parameter classes
4. **MPI Coordination Patterns** - Unique ID and port assignment
5. **EJFAT URI Patterns** - Producer, consumer, and back-to-back formats
6. **Deserialization Strategies** - HDF5, pickle, and raw formats
7. **Error Handling** - Import errors, timeouts, and result checking
8. **Testing Strategies** - Unit tests and integration testing
9. **Performance Tuning** - Throughput and latency optimization
10. **Troubleshooting Guide** - Common issues and solutions

## Using the Skill

### Automatic Triggering

The skill will automatically be invoked when you mention:
- "e2sar"
- "ejfat"
- "lclstreamer"
- "e2sar integration"
- "ejfat transport"

### Manual Invocation

```bash
# In Claude Code, invoke the skill:
/e2sar-integration
```

### Example Questions

The skill can help with:
- "How do I integrate E2SAR into my streaming application?"
- "What's the pattern for MPI rank coordination with E2SAR?"
- "How do I configure back-to-back testing without EJFAT hardware?"
- "What's the EJFAT URI format for producers vs consumers?"
- "How do I deserialize HDF5 data from E2SAR?"

## Reference Implementation

This LCLStreamer repository serves as the reference implementation for the skill.

### Key Files Referenced by Skill

- `src/lclstreamer/models/parameters.py` - Parameter model patterns
- `src/lclstreamer/data_handlers/streaming/e2sar.py` - Segmenter implementation
- `src/lclstreamer/event_data_sources/e2sar/event_sources.py` - Reassembler implementation
- `examples/e2sar-*.yaml` - Configuration examples
- `E2SAR_INTEGRATION.md` - Comprehensive documentation
- `IMPLEMENTATION_SUMMARY.md` - Technical implementation details

## Skill Contents

The skill documentation includes:

### Code Patterns
```python
# Example from skill - Segmenter pattern
class E2SARDataHandler:
    def __init__(self, parameters):
        self._rank = MPI.COMM_WORLD.Get_rank()
        self._data_id = (0x0500 + self._rank) & 0xFFFF
        # ... full implementation pattern
```

### Configuration Examples
```yaml
# Example from skill - Producer configuration
data_handlers:
  - type: E2SARDataHandler
    use_control_plane: true
    rate_gbps: 10.0
    mtu: 9000
```

### Testing Strategies
```bash
# Example from skill - Back-to-back testing
# Terminal 1: Consumer
mpirun -n 2 lclstreamer --config examples/e2sar-back-to-back-consumer.yaml

# Terminal 2: Producer
mpirun -n 2 lclstreamer --config examples/e2sar-back-to-back-producer.yaml
```

## Updating the Skill

If you make changes to the E2SAR integration in this repository, consider updating the skill:

1. **Edit the skill**:
   ```bash
   $EDITOR ~/.claude/skills/e2sar-integration/skill.md
   ```

2. **Update version number** in frontmatter:
   ```yaml
   version: 1.1.0  # Increment version
   ```

3. **Add new patterns** to the relevant sections

4. **Test the updates** by asking Claude Code questions about the new patterns

## Skill Structure

```
~/.claude/skills/e2sar-integration/
├── skill.md          # Main skill with all patterns (650+ lines)
└── README.md         # Skill overview and metadata
```

## Benefits of Having a Skill

1. **Reusable Knowledge** - Patterns available across all Claude Code sessions
2. **Consistent Implementation** - Follow proven patterns from reference implementation
3. **Quick Troubleshooting** - Common issues and solutions at your fingertips
4. **Future Projects** - Apply patterns to new streaming frameworks
5. **Team Knowledge Sharing** - Team members can invoke the same skill

## Related Skills

- **ejfat-workflow** - General EJFAT ecosystem patterns
- **cmake-helper** - Building E2SAR from source
- **python-scaffold** - Python project structure

## Verification

To verify the skill is loaded in Claude Code:

```bash
# The skill should appear in the system when you mention "e2sar"
# Try asking: "Show me the E2SAR Segmenter pattern"
```

## Skill Maintenance

**Created**: March 15, 2026
**Based on**: LCLStreamer E2SAR integration (this repository)
**Maintainer**: Update as E2SAR patterns evolve
**Changelog**: Track in `~/.claude/skills/e2sar-integration/skill.md` frontmatter

## Further Information

- **Skill Directory**: `~/.claude/skills/e2sar-integration/`
- **Skills Index**: `~/.claude/skills/SKILLS_INDEX.md`
- **This Project Documentation**: `E2SAR_INTEGRATION.md`
- **Implementation Summary**: `IMPLEMENTATION_SUMMARY.md`

---

*This skill makes the E2SAR integration patterns from this repository available globally across all Claude Code sessions.*
