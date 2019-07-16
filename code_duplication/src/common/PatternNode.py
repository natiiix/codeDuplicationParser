class PatternNode:
    """
    More abstract representation of multiple similar TreeNodes.

    Attributes:
        nodes {List[TreeNode]} -- List of TreeNodes with the same skeleton.
        weight {int} -- Weight of the entire node's tree.
        value {string} -- Common string representation of all the nodes.
        children {List[PatternNode]} -- List of node's direct children.
    """

    def __init__(self, node1, node2, weight, value=None):
        """
        Creates a new PatternNode from two nodes and their common value.

        Arguments:
            node1 {TreeNode} -- First TreeNode sharing common skeleton.
            node2 {TreeNode} -- Second TreeNode sharing common skeleton.
            weight {int} -- Weight of the entire node's tree.
            value {string} -- String representation common for all the nodes.
                              None if the PatternNode represents a hole.
        """
        self.nodes = [node1, node2]
        self.weight = weight
        self.value = value or f"Hole(weight={weight})"
        self.children = []

    def add_nodes(self, *nodes):
        self.nodes.extend(nodes)

    def add_children(self, *children):
        self.children.extend(children)

    def __eq__(self, other):
        if not isinstance(other, PatternNode) or other.value != self.value or \
                len(other.children) != len(self.children):
            return False

        for i, c in enumerate(other.children):
            if c != self.children[i]:
                return False

        return True

    def __ne__(self, other):
        return not self.__eq__(other)
