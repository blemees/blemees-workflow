# Roles

This workflow defines these roles:

## `pm` — Product Manager

Owns refinement; turns raw issues into ready-for-dev tickets or closes them as won't-fix

- **Processes**: refinement (owner)
- **Wakes on**: new raw issues, bounce-backs from developer
- **Does not**: decide architecture, implement tickets

## `developer` — Developer

Picks up ready tickets, implements them, and lands them on main

- **Processes**: inner loop (owner)
- **Wakes on**: new picked_up tickets, revisions requested on staged PRs
- **Does not**: scope tickets, decide whether a bug is won't-fix
