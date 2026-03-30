package Tree;

public class CreateBiTree {
    //创建二叉树
    public static TreeNode createTree(Integer[] arr){
        if(arr == null || arr.length == 0){
            return null;
        }
        return buildTree(arr, 0);
    }

    public static TreeNode buildTree(Integer[] arr, int index){
        //边界检查:如果索引超出数组范围或当前与元素为null，则返回null
        if(index >= arr.length || arr[index] == null){
            return null;
        }

        //创建当前节点
        TreeNode root = new TreeNode(arr[index]);

        //计算左子树在数组中的索引：2*index + 1
        int leftChildIndex = 2 * index + 1;
        //计算柚子树在数组中的索引：2*index + 2
        int rightChildIndex = 2 * index + 2;

        //递归构建左右子树
        root.left = buildTree(arr, leftChildIndex);
        root.right = buildTree(arr, rightChildIndex);

        return root;
    }
    //层序遍历二叉树
    public static void levelOrderTraversal(TreeNode root){
        if(root == null){
            System.out.println("二叉树为空");
            return;
        }

        //计算树的高度
        int height = getHeight(root);

        //按层遍历
        System.out.println("层序遍历结果:");
        for(int level = 0; level < height; level++){
            printLevel(root, level);
        }
        System.out.println();
    }

    //计算树的高度
    public static int getHeight(TreeNode root){
        if(root == null){
            return 0;
        }

        int leftHeight = getHeight(root.left);
        int rightHeight = getHeight(root.right);
        return leftHeight > rightHeight ? leftHeight + 1 : rightHeight + 1;
    }

    //打印指定层级的所有节点
    public static void printLevel(TreeNode root, int level){
        if(root == null){
            return;
        }

        if(level == 0){
            System.out.print(root.data + " ");
        } else if(level > 0){
            printLevel(root.left, level - 1);
            printLevel(root.right, level - 1);
        }
    }

    //按层层序遍历二叉树
    public static void levelOrderTraversalByTree(TreeNode root){
        if(root == null) {
            System.out.println("二叉树为空");
            return;
        }

        int height = getHeight(root);

        for(int level = 0; level < height; level++){
            System.out.println("第" + (level + 1) + "层：");
            printLevel(root, level);
            System.out.println();
        }
    }
}
