Task: Add total_stock()
Add a new method to InventoryService:
def total_stock(self) -> int:
    ...
Requirements
- Return the sum of quantities of all inventory items.
- Return 0 when the repository is empty.
- Preserve all existing public behavior.
- Keep the implementation simple and consistent with the existing codebase.
Testing
Add tests covering:
- multiple inventory items,
- an empty repository.
Run:
- the complete pytest suite,
- git diff --check.
Constraints
- Do not stage any files.
- Do not commit anything.